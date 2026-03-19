# MULTI_ASSETS — Contexte projet pour Claude

## Qu'est-ce que ce projet ?
Bot de trading algorithmique multi-paires sur **Binance Spot** (USDC comme quote).
Architecture : backtest walk-forward adaptatif → sélection des meilleurs paramètres →
exécution live toutes les 2 minutes par paire, en parallèle.

## Pipeline complet d'une décision de trading
```
1. fetch_historical_data()        ← API Binance ou cache pickle (TTL 30j)
2. prepare_base_dataframe()       ← OHLCV + validation intégrité
3. calculate_indicators()         ← EMA, StochRSI, ADX, TRIX (Cython ou Python)
4. run_all_backtests()            ← 4 scénarios × N paramètres × walk-forward
   └─ walk_forward.py             ← OOS gates : Sharpe≥0.8, WinRate≥30%, decay≥0.15
5. select_best_by_calmar()        ← meilleur scénario (ratio Calmar)
6. generate_buy/sell_checker()    ← closures pures depuis signal_generator.py
7. monitor_and_trade_for_pair()   ← décision finale + sizing
   ├─ compute_position_size_*()   ← 3 modes: risk (défaut), fixed, volatility_parity
   ├─ safe_market_buy()           ← idempotence via origClientOrderId
   ├─ place_exchange_stop_loss()  ← STOP_LOSS_LIMIT natif exchange
   └─ log_trade()                 ← journal JSONL append-only
8. schedule (2 min)               ← boucle principale via `schedule` library
```

## Modules et responsabilités
| Module | Responsabilité |
|--------|---------------|
| `MULTI_SYMBOLS.py` | Orchestrateur principal (~1634 lignes, refactorisé C-01→C-13) |
| `bot_config.py` | Config singleton + décorateurs |
| `exchange_client.py` | Client Binance robuste + rate limiter |
| `state_manager.py` | Persistance JSON+HMAC |
| `signal_generator.py` | Closures buy/sell pures |
| `backtest_runner.py` | Moteur backtest (Cython first) |
| `indicators_engine.py` | Indicateurs TA (Cython first) |
| `walk_forward.py` | Métriques OOS + gates anti-overfit |
| `backtest_orchestrator.py` | Coordination WF, assemblage résultats par scénario |
| `order_manager.py` | Cycle de vie des ordres SL (place, check, cancel) |
| `position_reconciler.py` | Réconciliation positions / soldes avec l'exchange au démarrage |
| `position_sizing.py` | 3 modes de sizing |
| `data_fetcher.py` | Fetch + validation données |
| `cache_manager.py` | Cache disque pickle (TTL 30j) |
| `trade_journal.py` | Journal JSONL thread-safe |
| `watchdog.py` | Surveillance processus + heartbeat |
| `exceptions.py` | Hiérarchie d'exceptions typée |

## Contraintes exchange critiques
- **Binance Spot uniquement** : pas de Futures, pas de leverage, pas de TRAILING_STOP_MARKET
- Quote : **USDC** (pas USDT)
- Filtres sur chaque paire : `LOT_SIZE` (stepSize), `MIN_NOTIONAL` (min 5 USDC en backtest)
- Rate limite Binance Spot : 1200 req/min → token bucket à 18 req/s (marge de 10%)
- `recvWindow = 60 000 ms` pour absorber les dérives horloge
- Timestamp offset calculé précisément : `offset = serverTime - localTime - latence/2 - 500ms`

## État global (`bot_state`)
```python
bot_state = {
    'emergency_halt': bool,          # kill-switch global
    'emergency_halt_reason': str,
    '_daily_pnl_tracker': {          # tracker perte journalière
        'YYYY-MM-DD': {'total_pnl': float, 'trade_count': int}
    },
    'BTCUSDC': PairState,            # une clé par paire active
    'SOLUSDC': PairState,
    # ...
}
```

## Optimisations déjà validées (bench)
| Paramètre | Avant | Après optimisé | Impact |
|-----------|-------|---------------|--------|
| `atr_multiplier` | 5.5 | 8.0 | PnL +22.5%, DD -0.9pp |
| `risk_per_trade` | 5.0% | 5.5% | Calmar max 2.004 |
| `stoch_rsi_sell_exit` | 0.2 | 0.4 | PnL +2%, DD -1pp |
| MTF filter 4h | désactivé | activé | benchmark positif |
| `breakeven_trigger_pct` | - | 2% | benchmark optimal |
| `stop_loss_cooldown` | - | 12 candles | benchmark optimal |

## Ce qui NE doit PAS changer sans benchmark
- `backtest_taker_fee` (0.0007) et `backtest_maker_fee` (0.0002) : figés pour reproductibilité
- `WF_SCENARIOS` : les 4 scénarios sont calibrés pour le crypto trend-following
- `OOS_DECAY_MIN = 0.15` : seuil anti-overfit (ratio OOS/FS Sharpe)
- Séquence de protection : `daily_loss_limit` → `oos_blocked` → `emergency_halt`

## Infrastructure de déploiement
- **Windows** : PM2 (`config/ecosystem.config.js`) + watchdog Python
- Heartbeat : `states/heartbeat.json` (fraîcheur < 10 minutes)
- Logs rotatifs : `code/logs/` (PM2) + `trading_bot.log` (Python logging)
- Max 5 restarts/heure, min 30s uptime pour compter comme démarrage réussi
