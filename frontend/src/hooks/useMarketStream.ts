import { useEffect, useRef, useState } from 'react';
import { WS_BASE } from './api';
import type { Tick } from './api';

// Live market stream over the backend WebSocket, with connection state and
// stale-data flagging. Reconnects automatically; a dead feed never fakes ticks.
export function useMarketStream(symbols: string[] = []) {
  const [ticks, setTicks] = useState<Record<string, Tick>>({});
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let retry = 1000;
    let timer: ReturnType<typeof setTimeout>;

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(WS_BASE);
      wsRef.current = ws;
      ws.onopen = () => { setConnected(true); retry = 1000; };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'tick' && msg.tick?.symbol) {
            setTicks((prev) => ({ ...prev, [msg.tick.symbol]: msg.tick as Tick }));
          }
        } catch { /* ignore malformed frames */ }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) { timer = setTimeout(connect, retry); retry = Math.min(retry * 1.5, 15000); }
      };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => { closed = true; clearTimeout(timer); wsRef.current?.close(); };
  }, []);

  return { ticks, connected };
}
