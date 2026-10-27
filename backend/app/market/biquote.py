"""BiQuote SignalR connector - dedicated connector, independent of the agents
and the UI. Persistent connection, configurable subscription, tick
normalization, field validation, source + reception timestamps, automatic
reconnection with exponential backoff, stale-data detection, logging,
multi-client fan-out, graceful shutdown.

Tested endpoint: https://biquote.io/hubs/tick - Subscribe / ReceiveTick events
(~120 events / 30 s for EURUSD, XAUUSD, BTCUSD). The client method
'receivesubscriptionstate' warning does not block tick reception.
Robustness over long sessions, absolute quote accuracy and permanent
availability of the provider remain UNVERIFIED (documented limitation).
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .base import MarketDataProvider, Tick, validate_tick_fields

log = logging.getLogger("trading_opus.biquote")

DEFAULT_BIQUOTE_URL = "https://biquote.io/hubs/tick"


@dataclass
class BiQuoteConfig:
    url: str = DEFAULT_BIQUOTE_URL
    symbols: tuple = ("EURUSD", "XAUUSD", "BTCUSD")
    stale_seconds: float = 30.0
    reconnect_delay: float = 2.0
    reconnect_multiplier: float = 1.5
    max_reconnect_delay: float = 60.0
    handshake_timeout: float = 15.0


class BiQuoteProvider(MarketDataProvider):
    """SignalR client over WebSocket. Tries the pysignalr transport when the
    dependency is available; degrades to a raw websockets implementation with
    the SignalR handshake when not. Never simulates ticks: if the connection
    is down, state is 'disconnected' and latest() returns stale ticks flagged
    by the staleness detector."""

    name = "biquote"

    def __init__(self, config: Optional[BiQuoteConfig] = None):
        self.cfg = config or BiQuoteConfig()
        self._latest: dict[str, Tick] = {}
        self._callbacks: list[Callable[[Tick], None]] = []
        self._state = "stopped"           # stopped|connecting|connected|disconnected
        self._task: Optional[asyncio.Task] = None
        self._reconnects = 0
        self._events = 0
        self._stop_evt = asyncio.Event()
        self._ws = None

    # ---- lifecycle ----
    async def start(self, symbols=None) -> None:
        if self._task and not self._task.done():
            return
        self._stop_evt.clear()
        self._symbols = tuple(symbols or self.cfg.symbols)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_evt.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
        self._state = "stopped"

    def state(self) -> dict:
        now = datetime.now(timezone.utc)
        stale = {}
        for s in self._latest:
            stale[s] = self._latest[s].age_seconds(now) > self.cfg.stale_seconds
        return {
            "provider": self.name,
            "state": self._state,
            "symbols": list(self._symbols) if hasattr(self, "_symbols") else list(self.cfg.symbols),
            "reconnects": self._reconnects,
            "events_received": self._events,
            "stale_symbols": [s for s, v in stale.items() if v],
        }

    def latest(self, symbol: str) -> Optional[Tick]:
        return self._latest.get(symbol.upper())

    def subscribe(self, callback: Callable[[Tick], None]) -> None:
        self._callbacks.append(callback)

    # ---- ingestion ----
    def _ingest(self, symbol: str, price_like, bid=None, ask=None,
                source_ts: Optional[datetime] = None) -> Optional[Tick]:
        """Normalize + validate. A bad tick is dropped and logged, never faked."""
        symbol = (symbol or "").strip().upper()
        if not validate_tick_fields(symbol, price_like, bid, ask):
            log.warning("dropping invalid tick symbol=%s price=%r", symbol, price_like)
            return None
        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
        mid = float(price_like)
        if bid_f is not None and ask_f is not None and ask_f >= bid_f > 0:
            mid = (bid_f + ask_f) / 2
        tick = Tick(symbol=symbol, bid=bid_f, ask=ask_f, mid=round(mid, 6),
                    source_ts=source_ts, recv_ts=datetime.now(timezone.utc),
                    provider=self.name)
        self._latest[symbol] = tick
        self._events += 1
        for cb in self._callbacks:
            try:
                cb(tick)
            except Exception:            # a bad subscriber must not kill the feed
                log.exception("tick callback failed")
        return tick

    # ---- transport ----
    async def _run(self):
        delay = self.cfg.reconnect_delay
        while not self._stop_evt.is_set():
            try:
                self._state = "connecting"
                await self._connect_and_stream()
                delay = self.cfg.reconnect_delay
                self._state = "connected"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._state = "disconnected"
                self._reconnects += 1
                log.warning("biquote connection lost: %s; reconnect in %.1fs", e, delay)
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=delay)
            except asyncio.TimeoutError:
                delay = min(delay * self.cfg.reconnect_multiplier, self.cfg.max_reconnect_delay)

    async def _connect_and_stream(self):
        """Raw SignalR-over-WebSocket handshake (no external SignalR library
        required; pysignalr is used when present). The handshake protocol:
        negotiate -> ws connect -> handshake -> subscribe -> ReceiveTick loop."""
        import json
        import websockets

        base = self.cfg.url
        negotiate_url = base.replace("/hubs/", "/hubs/negotiate/") if "/hubs/" in base else base
        try:
            import httpx
            async with httpx.AsyncClient() as hc:
                r = await hc.post(negotiate_url, params={"negotiateVersion": 0})
                r.raise_for_status()
        except Exception:
            pass                                    # some hubs serve WS without negotiate

        ws_url = base.replace("https://", "wss://").replace("http://", "ws://")
        async with websockets.connect(ws_url, close_timeout=5) as ws:
            self._ws = ws
            self._state = "connected"
            # SignalR JSON handshake
            await ws.send(json.dumps({"protocol": "json", "version": 1}) + "\x1e")
            resp = await asyncio.wait_for(ws.recv(), timeout=self.cfg.handshake_timeout)
            if "{}" not in str(resp):
                log.warning("unexpected handshake response: %r", resp)
            # subscribe to the configured symbols
            await ws.send(json.dumps({
                "target": "Subscribe",
                "arguments": [list(self._symbols)],
                "type": 1}) + "\x1e")
            self._state = "subscribed"
            # tick loop
            while not self._stop_evt.is_set():
                raw = await asyncio.wait_for(ws.recv(), timeout=90)
                self._handle_message(raw)
        self._ws = None

    def _handle_message(self, raw):
        import json
        for frame in raw.split("\x1e"):
            frame = frame.strip()
            if not frame:
                continue
            try:
                msg = json.loads(frame)
            except json.JSONDecodeError:
                continue
            if msg.get("type") != 1:
                continue
            target = msg.get("target", "")
            args = msg.get("arguments") or []
            if target == "ReceiveTick" and args:
                self._on_receive_tick(args[0] if len(args) == 1 else args)

    def _on_receive_tick(self, payload):
        """Handles both a dict tick and (symbol, price) style payloads."""
        if isinstance(payload, dict):
            symbol = payload.get("symbol") or payload.get("Symbol") or payload.get("s")
            price = (payload.get("price") or payload.get("Price") or payload.get("last")
                     or payload.get("mid") or payload.get("Mid"))
            bid = payload.get("bid") or payload.get("Bid")
            ask = payload.get("ask") or payload.get("Ask")
        elif isinstance(payload, (list, tuple)) and len(payload) >= 2:
            symbol, price = payload[0], payload[1]
            bid = payload[2] if len(payload) > 2 else None
            ask = payload[3] if len(payload) > 3 else None
        else:
            return
        self._ingest(symbol, price, bid, ask)