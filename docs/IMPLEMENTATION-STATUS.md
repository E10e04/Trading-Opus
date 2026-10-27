# État d'implémentation — rev. 2026-10-27 (branche `develop`)

Ce document complète le README avec un état factuel et honnête.

## Implémenté et committé

- **Audit du dépôt source** : les 11 `SKILL.md` de `Jakeschincariol/day-trader-skill`
  @ `3d0f4ef` lus individuellement ; licence MIT vérifiée ; matrice de
  correspondance skill → agent ; portages documentés dans `NOTICE`.
- **Cœur déterministe** (`backend/app/core/`) : calendrier NYSE 2026-27 (fériés,
  early closes, DST, fenêtre d'entrée), exit planner (stops or-mid/or-low/price/
  pct/atr, cibles 1R-3R, refus explicites, arrondis du skill), budget manager
  (4 limites, actions entières, refus), risk manager (15 contrôles, exit jamais
  bloquée, fingerprint d'ordre), entry timer (barre complète close > level,
  volume gate), scanner (RSI Wilder, 5 setups daily, ORB), timemachine (MC
  seedé, history réelle, verdict break-even).
- **Moteur paper trading** (`core/paper.py`) : market/limit/stop/stop-limit,
  bid/ask + slippage + frais, cash/positions/P&L réalisé et latent/exposition,
  historique de fills, refus (short off, cash insuffisant).
- **Connecteur BiQuote** (`market/biquote.py`) : handshake SignalR JSON,
  `Subscribe`/`ReceiveTick`, reconnexion exponentielle, staleness, fan-out,
  validation de champs, shutdown propre — derrière l'interface `MarketDataProvider`.
- **11 agents** (`agents/`) : un par skill, logique propre, état, historique,
  audit (skill ref + modèle + instrument + durée), erreurs capturées ;
  News/Analyst/Pelosi disent honnêtement quand la source n'est pas connectée.
- **Orchestrateur** : pipeline d'un trade avec Risk obligatoire, propositions
  (accepter/modifier/refuser, ordre modifié re-passe Risk), agents
  désactivables, audit persisté.
- **Persistance** SQLAlchemy (portefeuilles, positions, ordres, agent runs,
  propositions, alertes) — SQLite.
- **OpenRouter** (`ai/openrouter.py`) : clé backend only, budget mensuel,
  listing modèles, jamais loguée.
- **API FastAPI** (`main.py`) : REST (système, agents, marché, paper, proposals,
  openrouter, historique) + WebSocket de diffusion ; les ordres manuels passent
  par la Risk gate.
- **Frontend** React 18 + TS + Vite : thèmes sombre/clair **conçus séparément**
  (pas une inversion), préférence persistante, pré-paint sans flash, chiffres
  tabulaires, badges d'état, stream WS avec reconnexion, pages vue d'ensemble /
  marchés / agents / ordres / paramètres / diagnostic, affichage STALE,
  messages d'indisponibilité honnêtes.
- **Desktop** : Electron 37.2 (main sécurisé : contextIsolation, sandbox, pas
  de nodeIntegration ; preload minimal), scripts `dist:win` (NSIS x64) et
  `dist:mac` (dmg x64, min macOS 11.0). **Electron 38+ exclu** : il a retiré
  le support de macOS 11 (annonce officielle electronjs.org).
- **Tests backend** : `test_fidelity.py` (rejoue chaque chiffre de l'exemple du
  dépôt : stop 358.32, risque 5.86, cible 370.04, 8 actions, 46.88 au stop,
  BLOCK 9 actions 52.74, fenêtre 09:45, exit jamais bloquée), `test_agents.py`
  (11 agents, pipeline e2e, gates Broker, honnêteté offline), `test_market_paper.py`
  (parsing SignalR, staleness, fills, P&L, ordres conditionnels).
- **Tests frontend** : Vitest (résolution de thème, contrat des palettes).
- **Workflows** : CI (pytest + vitest + tsc/vite build) et release desktop
  (Windows NSIS + macOS dmg x64, draft release sur tag `v*`).

## Non terminé — documenté sans détour

1. **Clients HTTP de production** pour Yahoo RSS/chart, SEC EDGAR, House Clerk :
   les agents acceptent des données injectées (tests avec stubs, y compris des
   valeurs réelles du dépôt : TSLA +25.5 % de revenus, MOVING 3.31 %, BE $1,000,001-$5,000,000).
   Le câblage `httpx` dans chaque agent reste à faire.
2. **Pages frontend restantes** : détail instrument, graphiques, positions,
   stratégies, journal, notifications ; le formulaire de saisie d'ordre complet
   (la vue Ordres actuelle est un résumé).
3. **Builds desktop non produits** : l'environnement n'exécute pas npm. Le
   workflow de release est prêt mais **aucun installateur n'est déclaré
   vérifié** tant que la CI n'a pas construit et testé les artefacts.
4. **Activation CI** : les workflows vivent dans `workflows/` car le chemin
   `.github/` n'est pas écrivable par l'outil utilisé. Commande de déplacement :

   ```bash
   mkdir -p .github/workflows
   git mv workflows/ci-full.yml .github/workflows/ci-full.yml
   git mv workflows/desktop-release.yml .github/workflows/desktop-release.yml
   git rm workflows/ci.yml        # remplacé par ci-full.yml
   ```

5. **BiQuote** : la réception initiale (test préalable : ~120 ticks/30 s pour
   EURUSD/XAUUSD/BTCUSD) est intégrée ; la robustesse longue durée et la
   couverture réelle des symboles restent à valider en production.

## Comment vérifier

```bash
cd backend && python -m pytest tests -q     # doit passer : fidélité + agents + paper + marché
cd frontend && npm install && npm test && npm run build
```
