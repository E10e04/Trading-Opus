"""Tests for the BiQuote connector (message parsing, validation, staleness)
and the paper trading engine (fills, fees, P&L, conditional orders).
Live-network tests are separated: see test_biquote_live.py (skipped by default).
"""
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from app.market.base import validate_tick_fields, Tick              # noqa: E402
from app.market.biquote import BiQuoteProvider                       # noqa: E402
from app.core.paper import PaperEngine, PaperTradingError            # noqa: E402


# ---------------- tick validation ----------------
def test_tick_validation_rejects_bad():
    assert validate_tick_fields("EURUSD", 1.085, 1.0848, 1.0852)
    assert not validate_tick_fields("", 1.0)
    assert not validate_tick_fields("EURUSD", None)
    assert not validate_tick_fields("EURUSD", -5)
    assert not validate_tick_fields("EURUSD", "abc")
    assert not validate_tick_fields("EURUSD", 1.0, bid=-1)


def test_tick_mid_from_bid_ask():
    p = BiQuoteProvider()
    t = p._ingest("EURUSD", 1.0850, bid=1.0848, ask=1.0852)
    assert t.mid == 1.085                  # (bid+ask)/2 wins over a single price
    assert t.symbol == "EURUSD"


def test_ingest_drops_invalid_and_keeps_valid():
    p = BiQuoteProvider()
    assert p._ingest("XAUUSD", None) is None
    t = p._ingest("XAUUSD", 2650.40)
    assert t is not None and t.mid == 2650.40


def test_receive_tick_message_parsing():
    p = BiQuoteProvider()
    raw = ('{"type":1,"target":"ReceiveTick","arguments":[{"symbol":"BTCUSD","bid":67400.1,"ask":67410.9}]}\x1e')
    p._handle_message(raw)
    t = p.latest("BTCUSD")
    assert t is not None
    assert abs(t.mid - 67405.5) < 0.01
    # non-invocation frames are ignored
    p._handle_message('{"type":6}\x1e')
    p._handle_message('not json\x1e')
    assert p._events == 1


def test_stale_detection():
    p = BiQuoteProvider()
    old = datetime.now(timezone.utc) - timedelta(seconds=120)
    t = Tick(symbol="EURUSD", bid=None, ask=None, mid=1.09,
             source_ts=old, recv_ts=old, provider="biquote")
    p._latest["EURUSD"] = t
    state = p.state()
    assert "EURUSD" in state["stale_symbols"]


def test_subscriber_failure_does_not_kill_feed():
    p = BiQuoteProvider()
    def bad_cb(t):
        raise RuntimeError("boom")
    p.subscribe(bad_cb)
    got = []
    p.subscribe(got.append)
    t = p._ingest("EURUSD", 1.09)
    assert t is not None
    assert got[0].symbol == "EURUSD"


# ---------------- paper engine ----------------
def test_market_buy_with_fees():
    e = PaperEngine(commission_pct=0.0, slippage_bps=5, spread_pct=0.02)
    e.create_portfolio("main", 10000)
    o = e.submit("main", "BTCUSD", "buy", qty=1, type_="market", mid_price=50000)
    assert o.status == "filled"
    assert o.filled_price > 50000               # ask + slippage
    pf = e.get("main")
    assert pf.cash < 10000
    assert pf.positions["BTCUSD"].qty == 1


def test_sell_rejected_when_flat():
    e = PaperEngine()
    e.create_portfolio("main", 10000)
    try:
        e.submit("main", "TSLA", "sell", qty=1, type_="market", mid_price=100)
        assert False, "must refuse"
    except PaperTradingError:
        pass


def test_buy_rejected_without_cash():
    e = PaperEngine()
    e.create_portfolio("main", 1000)
    try:
        e.submit("main", "BTCUSD", "buy", qty=1, type_="market", mid_price=50000)
        assert False, "must refuse"
    except PaperTradingError:
        pass


def test_roundtrip_realized_pnl():
    e = PaperEngine(commission_pct=0.0, slippage_bps=0, spread_pct=0.0)
    e.create_portfolio("main", 10000)
    e.submit("main", "TSLA", "buy", qty=10, type_="market", mid_price=100)
    e.submit("main", "TSLA", "sell", qty=10, type_="market", mid_price=110)
    pf = e.get("main")
    assert pf.realized_pnl == 100.0            # 10 shares x 10
    assert pf.positions["TSLA"].qty == 0
    assert pf.equity({}) == 10100


def test_limit_order_fills_on_tick():
    e = PaperEngine()
    e.create_portfolio("main", 10000)
    o = e.submit("main", "EURUSD", "buy", qty=10000, type_="limit", limit_price=1.085)
    assert o.status == "working"
    fills = e.on_tick("main", "EURUSD", mid=1.080)     # price fell to the limit
    assert o.status == "filled"
    assert len(fills) == 1
    assert o.filled_price <= 1.085


def test_stop_loss_triggers_on_tick():
    e = PaperEngine()
    e.create_portfolio("main", 10000)
    e.submit("main", "XAUUSD", "buy", qty=1, type_="market", mid_price=2600)
    o = e.submit("main", "XAUUSD", "sell", qty=1, type_="stop", stop_price=2550)
    assert o.status == "working"
    fills = e.on_tick("main", "XAUUSD", mid=2540)      # dropped through the stop
    assert o.status == "filled"
    assert o.filled_price <= 2540                     # a stop can fill past its price
    assert e.get("main").positions["XAUUSD"].qty == 0


def test_valuation_and_exposure():
    e = PaperEngine()
    e.create_portfolio("main", 10000)
    e.submit("main", "BTCUSD", "buy", qty=0.1, type_="market", mid_price=50000)
    pf = e.get("main")
    prices = {"BTCUSD": 55000}
    assert abs(pf.market_value(prices) - 5500) < 1e-6
    assert abs(pf.unrealized_pnl(prices) - 500) < 1e-6
    exp = pf.exposure(prices)
    assert 0.4 < exp < 0.6


def test_order_types_validated():
    e = PaperEngine()
    e.create_portfolio("main", 10000)
    for args in (
        {"type_": "limit"},                                # missing limit_price
        {"type_": "stop", "stop_price": 1},                 # ok stop, but nothing to sell... covered elsewhere
    ):
        try:
            e.submit("main", "EURUSD", "buy", qty=1, **args)
            if args.get("type_") == "limit":
                assert False, "limit without price must refuse"
        except PaperTradingError:
            pass


def test_cancel_working_order():
    e = PaperEngine()
    e.create_portfolio("main", 10000)
    e.submit("main", "BTCUSD", "buy", qty=0.01, type_="market", mid_price=50000)
    o = e.submit("main", "BTCUSD", "sell", qty=0.01, type_="limit", limit_price=60000)
    e.cancel("main", o.order_id)
    assert o.status == "canceled"