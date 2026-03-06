# System Design — MULTI_ASSETS

## Vue d'ensemble

MULTI_ASSETS est un bot de trading algorithmique multi-paires fonctionnant sur **Binance Spot** (quote USDC uniquement). Il tourne 24/7 sur Windows, supervisé par PM2 + un watchdog Python interne. Le cycle de trading est **synchrone et horaire** (1 bougie 1h = 1 décision).

```
┌─────────────────────────────────────────────────────────────┐
│                        PM2 / Watchdog                        │
│   ┌─────────────────────────────────────────────────────┐   │
│   │                   MULTI_SYMBOLS.py                   │   │
│   │   (orchestrateur principal — boucle scheduler)       │   │
│   └────────┬────────────────────────────────────────────┘   │
│            │ schedule.every(1h)                              │
│   ┌────────▼────────────────────────────────────────────┐   │
│   │              Pipeline par paire (thread)             │   │
│   │  DataFetcher → SignalGen → PositionSizing → Execute  │   │
│   └────────┬────────────────────────────────────────────┘   │
│            │                                                  │
│   ┌────────▼──────────┐   ┌──────────────────────────────┐  │
│   │  StateManager     │   │  ExchangeClient (Token Bucket)│  │
│   │  (JSON+HMAC-SHA)  │   │  (Binance Spot API)           │  │
│   └───────────────────┘   └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Modules et responsabilités

| Module | Responsabilité principale | Dépendances clés |
|--------|--------------------------|-----------------|
| `MULTI_SYMBOLS.py` | Orchestrateur, scheduler, lifecycle du bot | Tous les modules |
| `bot_config.py` | Singleton `Config`, chargé via `Config.from_env()` | `os.environ` |
| `exchange_client.py` | Binance API, token bucket, idempotence, clock sync | `python-binance`, `Config` |
| `state_manager.py` | Persistance JSON+HMAC, lecture/écriture `bot_state` | `Config` (clé HMAC) |
| `data_fetcher.py` | Téléchargement OHLCV 1h + 4h, cache pickle | `exchange_client`, `cache_manager` |
| `cache_manager.py` | Cache OHLCV pickle (TTL 30 jours) | fichier système |
| `signal_generator.py` | Calcul signaux BUY/SELL par scénario WF | `indicators_engine`, `backtest_runner` |
| `indicators_engine.py` | Calcul indicateurs techniques (StochRSI, SMA, ADX, TRIX, EMA) | `indicators.pyd` ou fallback Python |
| `backtest_runner.py` | Exécution backtest WF_SCENARIOS, fees figés | `backtest_engine_standard.pyd`, `walk_forward` |
| `walk_forward.py` | Walk-forward ancré, OOS gates, sélection scénario | `backtest_runner` |
| `position_sizing.py` | Calcul de la taille de position (3 modes) | `Config` |
| `trade_helpers.py` | `safe_market_buy/sell`, placement SL, réconciliation | `exchange_client`, `state_manager` |
| `market_analysis.py` | Analyse contexte marché, filtres MTF 4h | `data_fetcher`, `indicators_engine` |
| `error_handler.py` | Centralisation des erreurs, alertes critiques | `email_utils` |
| `email_utils.py` | Envoi emails (SMTP Gmail) | `email_templates`, `Config` |
| `email_templates.py` | Templates HTML des alertes email | — |
| `trade_journal.py` | Journalisation JSONL des trades | fichier système |
| `display_ui.py` | Affichage console (rich) | — |
| `watchdog.py` | Surveillance processus, redémarrage auto | `subprocess`, `Config` |
| `preload_data.py` | Pré-chargement OHLCV au démarrage | `data_fetcher` |
| `timestamp_utils.py` | Calcul et resync de l'offset horloge | `exchange_client` |
| `exceptions.py` | Hiérarchie d'exceptions custom | — |
| `benchmark.py` | Calcul métriques de performance (Sharpe, Calmar, etc.) | `numpy`, `pandas` |

---

## Pipeline de trading (cycle horaire)

```
1. SCHEDULER TICK (toutes les heures, aligné bougie clôturée)
   │
2. PRECHECK
   ├── emergency_halt ? → stop total
   ├── daily_loss_limit dépassé ? → skip achats
   └── reconcile_positions_with_exchange() → sync soldes
   │
3. DATA FETCH (par paire, cache d'abord)
   ├── OHLCV 1h → DataFrame (close, high, low, volume)
   └── OHLCV 4h → MTF filter (EMA18 > EMA58, shift(1))
   │
4. SIGNAL GENERATION (par paire)
   ├── Calcul indicateurs (StochRSI, SMA200, ADX, TRIX)
   ├── Sélection scénario WF (walk_forward.get_best_scenario)
   ├── oos_blocked ? → skip achat
   └── Signal BUY / SELL / HOLD
   │
5. POSITION SIZING (si BUY)
   ├── Mode : risk-based / fixed-notional / volatility-parity
   ├── LOT_SIZE stepSize arrondi
   └── MIN_NOTIONAL check
   │
6. EXECUTION (avec _pair_execution_locks[pair])
   ├── BUY → safe_market_buy (idempotent)
   │   └── Immédiatement : STOP_LOSS_LIMIT exchange
   ├── SELL → safe_market_sell (idempotent)
   │   └── Annulation SL existant
   └── Partials / breakeven / trailing (logique interne)
   │
7. STATE PERSISTENCE
   ├── _bot_state_lock.acquire()
   ├── update pair_state (position, sl_order_id, etc.)
   ├── save_bot_state() throttlé 5s (force=True si critique)
   └── trade_journal.log_trade()
   │
8. ALERTES EMAIL
   └── BUY/SELL/SL hit/daily loss/OOS block → email_utils.send()
```

---

## Structure de l'état persisté (`bot_state.json`)

```json
{
  "version": "JSON_V1",
  "timestamp": "2026-03-06T12:00:00Z",
  "emergency_halt": false,
  "daily_loss_usdc": 0.0,
  "daily_loss_date": "2026-03-06",
  "pairs": {
    "BTCUSDC": {
      "in_position": false,
      "entry_price": 0.0,
      "quantity": 0.0,
      "sl_order_id": null,
      "sl_exchange_placed": false,
      "sl_price": 0.0,
      "oos_blocked": false,
      "consecutive_failures": 0,
      "last_sl_hit_candle": null,
      "active_scenario": "StochRSI_SMA200_ADX"
    }
  }
}
```

---

## Flux de données inter-modules

```
bot_config.py ──────────────────────────────► tous les modules (Config singleton)
                                               
data_fetcher.py ──► cache_manager.py (R/W)
      │
      ▼
indicators_engine.py ──► indicators.pyd (Cython) ou Python fallback
      │
      ▼
signal_generator.py ──► backtest_runner.py ──► walk_forward.py
      │                        │
      │                        └──► backtest_engine_standard.pyd (Cython)
      ▼
market_analysis.py (MTF 4h filter)
      │
      ▼
position_sizing.py
      │
      ▼
trade_helpers.py ──► exchange_client.py ──► Binance Spot API
      │                    │
      │                    └──► timestamp_utils.py (clock sync)
      ▼
state_manager.py ──► states/bot_state.json (JSON+HMAC)
      │
      ▼
trade_journal.py ──► code/src/logs/trade_journal.jsonl
email_utils.py ──► SMTP Gmail (alertes critiques)
```

---

## Infrastructure de déploiement

| Composant | Rôle | Config |
|-----------|------|--------|
| PM2 | Supervisor de processus, redémarrage auto | `config/ecosystem.config.js` |
| `watchdog.py` | Surveillance interne, re-spawn si crash | `schedule.every(30s).do(check_health)` |
| `heartbeat.json` | Preuve de vie du bot (horodatage) | `states/heartbeat.json` |
| `.venv/` | Environnement Python isolé (3.13) | `requirements.txt` |
| `code/bin/` | Modules Cython compilés `.pyd` | `config/setup.py` |
| `code/logs/` | Logs applicatifs (rotation) | niveau INFO/WARNING/ERROR |
| `states/` | États persistés (JSON+HMAC) | lecture/écriture via `state_manager` |
| `cache/` | Cache OHLCV pickle (TTL 30j) | TTL géré par `cache_manager` |

### Démarrage du bot
```powershell
# Via PM2 (production)
pm2 start config/ecosystem.config.js

# Via script direct (debug)
.venv\Scripts\python.exe code/src/MULTI_SYMBOLS.py

# Vérification santé
pm2 status
pm2 logs multi-assets --lines 50
```

---

## Choix architecturaux clés (rationale)

| Choix | Pourquoi |
|-------|---------|
| **Binance Spot, pas Futures** | Pas de levier, pas de liquidation, adapté au capital initial de 10K USDC |
| **Scheduler synchrone, pas asyncio** | `python-binance` sync, PM2 gère la disponibilité, complexité réduite |
| **Windows + PM2** | Environnement de l'opérateur, PM2 multiplatform disponible via Node.js |
| **USDC uniquement (pas USDT)** | USDC = stablecoin réglementé, meilleure traçabilité fiscale |
| **Bougie 1h** | Compromis signal/bruit : assez fréquent pour capturer les tendances, assez lent pour les frais |
| **4 scénarios WF** | Diversification des modèles sans overfitting (StochRSI, +SMA200, +ADX, +TRIX) |
