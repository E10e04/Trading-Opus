import { useEffect, useState } from 'react';
import ReactDOM from 'react-dom/client';
import './styles/tokens.css';
import { loadTheme, applyTheme, watchSystem, type ThemeMode } from './theme';
import { apiGet } from './api';
import type { AgentStatus, FeedState } from './api';
import { useMarketStream } from './hooks/useMarketStream';

type Page = 'overview' | 'agents' | 'markets' | 'orders' | 'settings' | 'diagnostic';

const NAV: { key: Page; label: string }[] = [
  { key: 'overview', label: 'Vue d\'ensemble' },
  { key: 'markets', label: 'Marchés' },
  { key: 'agents', label: 'Agents IA' },
  { key: 'orders', label: 'Ordres & simulateur' },
  { key: 'settings', label: 'Paramètres' },
  { key: 'diagnostic', label: 'Diagnostic' },
];

function Badge({ state, children }: { state: 'ok' | 'warn' | 'err'; children: React.ReactNode }) {
  return <span className={'badge ' + state}>{children}</span>;
}

function fmt(n: number | null | undefined, d = 2): string {
  return n === null || n === undefined ? '—' : n.toFixed(d);
}

function App() {
  const [mode, setMode] = useState<ThemeMode>(() => loadTheme());
  const [page, setPage] = useState<Page>('overview');
  const [agents, setAgents] = useState<AgentStatus[] | null>(null);
  const [feed, setFeed] = useState<FeedState | null>(null);
  const [sysErr, setSysErr] = useState<string | null>(null);
  const { ticks, connected } = useMarketStream();

  useEffect(() => { applyTheme(mode); }, [mode]);
  useEffect(() => watchSystem(mode, () => applyTheme('system')), [mode]);

  useEffect(() => {
    const load = () => {
      apiGet<any>('/api/v1/system/status')
        .then((d) => { setAgents(d.agents); setFeed(d.biquote); setSysErr(null); })
        .catch((e) => setSysErr(String(e)));
    };
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  const tickRows = Object.values(ticks);

  return (
    <div style={{ display: 'flex', minHeight: '100vh' }}>
      <nav style={{
        width: 220, flexShrink: 0, padding: '16px 12px',
        background: 'var(--panel)', borderRight: '1px solid var(--border)',
        display: 'flex', flexDirection: 'column', gap: 4,
      }}>
        <div style={{ fontWeight: 600, fontSize: 16, padding: '8px 12px 16px' }}>
          Trading Opus
          <div className="muted" style={{ fontSize: 11, fontWeight: 400 }}>
            paper trading uniquement
          </div>
        </div>
        {NAV.map((n) => (
          <button key={n.key} onClick={() => setPage(n.key)}
            style={{
              textAlign: 'left', background: page === n.key ? 'var(--surface)' : 'transparent',
              border: 'none', borderRadius: 6, padding: '8px 12px', color: 'var(--text)',
              cursor: 'pointer',
            }}>
            {n.label}
          </button>
        ))}
        <div className="grow" />
        <select value={mode} onChange={(e) => setMode(e.target.value as ThemeMode)}
          style={{ padding: 6, background: 'var(--surface)', color: 'var(--text)',
                   border: '1px solid var(--border)', borderRadius: 4 }}>
          <option value="dark">Thème sombre</option>
          <option value="light">Thème clair</option>
          <option value="system">Système</option>
        </select>
      </nav>

      <main style={{ flex: 1, padding: 24, maxWidth: 1100 }}>
        {sysErr && (
          <div className="panel error" style={{ marginBottom: 16 }}>
            Connexion backend impossible ({sysErr}). Le backend FastAPI doit tourner
            sur 127.0.0.1:8000. Aucune donnée n'est simulée en attendant.
          </div>
        )}

        {page === 'overview' && (
          <>
            <h2 style={{ marginTop: 0 }}>Vue d'ensemble</h2>
            <div className="panel" style={{ marginBottom: 16 }}>
              <div className="row">
                <Badge state={connected ? 'ok' : 'err'}>
                  flux BiQuote {connected ? 'connecté' : 'déconnecté'}
                </Badge>
                {feed && <Badge state={feed.stale_symbols.length ? 'warn' : 'ok'}>
                  {feed.symbols.length} symboles · {feed.events_received} ticks
                </Badge>}
                {agents && <Badge state="ok">{agents.filter(a => a.state !== 'disabled').length} agents</Badge>}
              </div>
            </div>
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>Cotations en direct</h3>
              {tickRows.length === 0
                ? <span className="muted">Aucun tick reçu. Le fournisseur peut être
                    indisponible ; aucune cotation n'est inventée.</span>
                : <table className="fin">
                    <thead><tr><th>Symbole</th><th>Bid</th><th>Ask</th><th>Mid</th><th>Reçu</th></tr></thead>
                    <tbody>
                      {tickRows.map((t) => (
                        <tr key={t.symbol} className={t.stale ? 'warn' : ''}>
                          <td>{t.symbol}{t.stale ? ' (STALE)' : ''}</td>
                          <td className="price">{fmt(t.bid, 4)}</td>
                          <td className="price">{fmt(t.ask, 4)}</td>
                          <td className="price">{fmt(t.mid, 4)}</td>
                          <td className="muted">{new Date(t.recv_ts).toLocaleTimeString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>}
            </div>
          </>
        )}

        {page === 'agents' && (
          <>
            <h2 style={{ marginTop: 0 }}>Agents IA — 1 skill = 1 agent</h2>
            {agents === null ? <span className="loading">chargement…</span> :
              <table className="fin panel">
                <thead><tr><th>Agent</th><th>Skill source</th><th>Référence</th><th>Mission</th><th>État</th><th>Runs</th></tr></thead>
                <tbody>
                  {agents.map((a) => (
                    <tr key={a.key}>
                      <td>{a.name}</td>
                      <td className="mono">{a.skill}</td>
                      <td className="mono muted">{a.skill_ref?.slice(0, 7)}</td>
                      <td className="muted" style={{ maxWidth: 380 }}>{a.mission}</td>
                      <td><Badge state={a.state === 'running' ? 'warn' : 'ok'}>{a.state}</Badge></td>
                      <td className="price">{a.runs}</td>
                    </tr>
                  ))}
                </tbody>
              </table>}
            <p className="muted">
              Chaque agent exécute la logique de son skill du dépôt day-trader-skill
              (commit {agents?.[0]?.skill_ref?.slice(0, 7) ?? '…'}). Les agents
              déterministes (Risk, Budget, Exit…) n'utilisent pas de modèle IA.
            </p>
          </>
        )}

        {page === 'markets' && (
          <>
            <h2 style={{ marginTop: 0 }}>Marchés</h2>
            <div className="panel">
              {feed === null ? <span className="loading">chargement…</span> : (
                <>
                  <div className="row" style={{ marginBottom: 12 }}>
                    <Badge state={feed.state === 'connected' || feed.state === 'subscribed' ? 'ok' : 'err'}>
                      {feed.state}
                    </Badge>
                    <Badge state={feed.reconnects > 0 ? 'warn' : 'ok'}>
                      {feed.reconnects} reconnexions
                    </Badge>
                    <Badge state="ok">{feed.events_received} ticks reçus</Badge>
                  </div>
                  <div className="muted">
                    Symboles souscrits : {feed.symbols.join(', ')}. Un symbole non couvert
                    par le fournisseur est signalé, jamais simulé.
                  </div>
                </>
              )}
            </div>
          </>
        )}

        {page === 'orders' && (
          <>
            <h2 style={{ marginTop: 0 }}>Ordres &amp; simulateur</h2>
            <div className="panel muted">
              Saisie manuelle d'ordres (market/limit/stop) vers le moteur de paper
              trading. Tout ordre passe d'abord par la validation déterministe de
              l'agent Risk ; un ordre bloqué n'est jamais exécuté. Interface de saisie
              complète sur la page dédiée du build desktop.
            </div>
          </>
        )}

        {page === 'settings' && (
          <>
            <h2 style={{ marginTop: 0 }}>Paramètres</h2>
            <div className="panel">
              <label>
                Thème :{' '}
                <select value={mode} onChange={(e) => setMode(e.target.value as ThemeMode)}>
                  <option value="dark">Sombre</option>
                  <option value="light">Clair</option>
                  <option value="system">Système</option>
                </select>
              </label>
              <p className="muted">La préférence est persistée (localStorage) et
                partagée par les builds web et desktop.</p>
            </div>
          </>
        )}

        {page === 'diagnostic' && (
          <>
            <h2 style={{ marginTop: 0 }}>Diagnostic du système</h2>
            <div className="panel">
              {feed === null ? <span className="loading">chargement…</span> : (
                <ul>
                  <li>Flux BiQuote : <Badge state={feed.state === 'connected' ? 'ok' : 'warn'}>{feed.state}</Badge>
                    — {feed.events_received} ticks, {feed.reconnects} reconnexions,
                    staleness &gt; {feed.stale_symbols.length ? '' : '0'} symbole(s) périmé(s)</li>
                  <li>Backend : <Badge state={sysErr ? 'err' : 'ok'}>{sysErr ? 'injoignable' : 'ok'}</Badge></li>
                  <li>Mode d'exécution : paper trading uniquement — aucune route courtier réel.</li>
                </ul>
              )}
            </div>
          </>
        )}
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
