# Trading Opus — application de trading multi-agents (paper trading)

Surveillance de marchés, coordination de 11 agents IA issus des skills
[day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) (MIT © 2026 Jake Schincariol),
propositions d'ordres, saisie manuelle et **simulation sans argent réel** (paper trading uniquement).

## Stack

| Couche | Choix | Remarque |
| --- | --- | --- |
| Frontend | React 18 + TypeScript + Vite | partagé web / Windows / macOS |
| Desktop | **Electron 37.x** | dernière lignée supportant **macOS 11 Big Sur** ; Electron 38+ exige macOS 12+ |
| Backend | Python 3.11 + FastAPI | API REST + WebSocket |
| Persistance | SQLite + SQLAlchemy | |
| Flux temps réel | BiQuote SignalR (`https://biquote.io/hubs/tick`) | connecteur dédié derrière interface `MarketDataProvider` |
| IA | OpenRouter (clé backend uniquement) | agents déterministes restent déterministes |

## Installation (dev)

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env       # puis renseigner OPENROUTER_API_KEY, SEC_USER_AGENT...
uvicorn app.main:app --reload --port 8000

# frontend + desktop
cd frontend
npm install
npm run dev                   # web: http://localhost:5173
npm run electron:dev          # desktop dev
```

## Tests

```bash
cd backend && pytest -q          # agents, moteur paper, risque (fidelity reel TSLA 2026-10-19)
cd frontend && npm test          # Vitest: thèmes, composants
cd frontend && npm run build     # tsc + vite build (vérifié par CI)
```

## Builds desktop & releases

```bash
cd frontend
npm run build
npm run dist:win     # installateur Windows x64 (NSIS)
npm run dist:mac     # .dmg + .pkg macOS x64, deployment target 11.0
```

CI (`.github/workflows/desktop-release.yml`) : sur tag `v*`, build des deux plateformes et création d'une **draft release** GitHub avec artefacts vérifiés.

## Configuration fournisseurs

- **BiQuote** : `BIQUOTE_URL`, `BIQUOTE_SYMBOLS` (défaut `EURUSD,XAUUSD,BTCUSD`). Connecteur SignalR indépendant, reconnexion auto, détection staleness.
- **OpenRouter** : `OPENROUTER_API_KEY` + `OPENROUTER_BASE_URL`. Clé jamais exposée au frontend ni aux logs. Test de connexion + sélection de modèles via `GET /api/v1/openrouter/status`.
- **SEC EDGAR** (agents News/Analyst) : `SEC_USER_AGENT="Prénom Nom prenom@exemple.com"` — sinon les requêtes EDGAR sont ignorées avec une note explicite.
- **House Clerk** (agent Pelosi) : public, aucune clé. PDF → pdftotext optionnel.

## Les 11 agents (1 skill = 1 agent)

| Agent | Skill source | Rôle | Statut |
| --- | --- | --- | --- |
| Scanner | trader-scanner | univers Nasdaq Trader, setups daily (breakout_20d, bull_flag, reclaim_ma20, RSI Wilder, oversold_bounce) + ORB 5m | déterministe |
| News | trader-news | RSS Yahoo + 8-K EDGAR, tags, MOVING ≥3 %, dédup | partiel (dépend réseau) |
| Entry | trader-entry | trigger = 1ʳᵉ barre complète clôturant > level, volume gate, fenêtre 09:45–15:30 ET | déterministe |
| Exit | trader-exit | stops or-mid/or-low/price/pct/atr, cible 2R/1R/price, refus explicites | déterministe |
| TimeMachine | trader-timemachine | 10 000 replays MC barre-à-barre, history réelle, verdict break-even 1/(1+R) | déterministe |
| Risk | trader-risk | 15 contrôles, exit jamais bloqué, fenêtre ET/UTC | déterministe backend (non désactivable par IA) |
| Budget | trader-budget | min de 4 limites, actions entières, bracket Alpaca-shaped (paper only) | déterministe |
| Broker | trader-broker | 3 gates → **moteur paper trading uniquement** (aucune route courtier réel) | déterministe |
| Alerts | trader-alerts | above/below/stop/target/pct_move/time/opening_bell, 1 ring par règle, horloge NYSE 2026-27 | déterministe |
| Analyst | trader-analyst | XBRL EDGAR, croissances vs N-1, Q4 derived, TTM, flag one-offs | partiel (dépend EDGAR) |
| Pelosi | trader-pelosi | PTRs House Clerk, montants en ranges, délai trade→filing | partiel (dépend Clerk + pdftotext) |

Chaque exécution d'agent est journalisée (agent, skill ref, instrument, statut, durée, résultats, erreurs).

## Avertissements

- **Paper trading uniquement.** Aucun ordre réel. Ce logiciel n'est pas un conseil financier.
- Les données Yahoo/EDGAR/Clerk sont gratuites, non officielles, peuvent être retardées ou indisponibles.
- Le TimeMachine est une simulation construite sur les 60 derniers jours — pas une prédiction.
- Les déclarations Congress sont **publiques et jusqu'à 45 jours après** la transaction (STOCK Act) : jamais un signal temps réel.
- Un stop est un plan, pas une garantie.
