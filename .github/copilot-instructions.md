# MULTI_ASSETS — Copilot Instructions

## Stack
- **Python 3.13** · Binance **Spot uniquement** (pas Futures) · Pandas 3.0 / NumPy 2.4
- Quote currency: **USDC** (jamais USDT) sur toutes les paires
- Venv: `.venv/` · Tests: `pytest tests/ -x -q` (depuis la racine du repo)
- PM2 + `code/src/watchdog.py` assurent la continuité de service

## Structure
```
code/src/      ← tous les modules Python
code/bin/      ← .pyd Cython compilés (backtest_engine_standard, indicators)
states/        ← bot_state.json (JSON_V1 + HMAC-SHA256)
cache/         ← cache OHLCV pickle (TTL 30 jours)
tests/         ← pytest (590 tests, 25 fichiers)
config/        ← ecosystem.config.js (PM2)
```

## Config & Credentials
- Singleton `Config` dans `bot_config.py`, chargé via `Config.from_env()`
- `Config.__repr__` **masque** api_key/secret_key — ne jamais logger `config` brut
- Vars d'env obligatoires : BINANCE_API_KEY, BINANCE_SECRET_KEY, SENDER_EMAIL,
  RECEIVER_EMAIL, GOOGLE_MAIL_PASSWORD

## Thread Safety — RÈGLE ABSOLUE
- `_bot_state_lock` (RLock) protège TOUTES les écritures dans `bot_state`
- `_pair_execution_locks[pair]` empêche l'exécution concurrente par paire
- `_oos_alert_lock` protège `_oos_alert_last_sent`
- `save_bot_state()` : throttlé 5s, utiliser `force=True` pour les saves critiques

## Stop-Loss — Règles critiques
- `TRAILING_STOP_MARKET` **n'existe pas sur Spot** → `NotImplementedError`
- Stop-loss = `STOP_LOSS_LIMIT` exchange-natif (pas manuel)
- Après chaque BUY, le SL **doit** être posé immédiatement → sinon `safe_market_sell` d'urgence
- `sl_order_id` et `sl_exchange_placed` persistés dans `pair_state`
- `recvWindow = 60000ms` (centralisé dans `config.recv_window`)

## Fees
- Live : `taker_fee=0.0007`, `maker_fee=0.0002`
- `backtest_taker_fee` / `backtest_maker_fee` sont **FIGÉS** et ne doivent JAMAIS
  être écrasés par les fees live. Modifier backtest_runner.py uniquement.

## État persisté
- Format `JSON_V1:` + HMAC-SHA256 (clé = `BINANCE_SECRET_KEY`)
- `PairState` TypedDict dans `MULTI_SYMBOLS.py` ; `_KNOWN_PAIR_KEYS` dans `state_manager.py`
- `StateError` sur HMAC mismatch → démarrage avec état vide + réconciliation API

## Protection du capital
- `daily_loss_limit_pct=0.05` (5% de 10 000 USDC) bloque les achats si dépassé
- 3 échecs consécutifs `save_bot_state()` → `emergency_halt = True`
- `oos_blocked=True` dans pair_state bloque les achats jusqu'à validation OOS
- OOS gates : Sharpe ≥ 0.8, WinRate ≥ 30%, decay ≥ 0.15

## Signaux & Backtest
- `WF_SCENARIOS` (MULTI_SYMBOLS.py) : 4 scénarios — StochRSI, +SMA200, +ADX, +TRIX
- Fenêtre backtest : 1095 jours glissants (`_fresh_start_date()` — jamais une variable figée)
- MTF filter 4h : EMA18 > EMA58, avec `shift(1)` sur les bougies 4h (anti look-ahead)
- Rate limiter : token bucket 18 req/s dans `exchange_client.py`
- Idempotence : check `origClientOrderId` avant chaque retry dans `safe_market_buy/sell`

## Validation systématique après chaque modification
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/<fichier>.py').read()); print('OK')"
pytest tests/ -x -q
```

## Interdictions absolues
- `except Exception: pass` ou `except Exception: ...` muet → toujours `logger.debug/warning/error`
- Logger `config.api_key` ou `config.secret_key` en clair
- Utiliser une `start_date` figée à l'import → utiliser `_fresh_start_date()`
- Modifier `backtest_taker_fee` au runtime
- Appeler `TRAILING_STOP_MARKET` sur Spot

## Contexte modulaire

Chaque module critique dispose d'un fichier de contexte dans `code/src/` :
- `backtest_runner.context.md` — contraintes du moteur backtest
- `exchange_client.context.md` — règles du client Binance (rate limiter, idempotence)
- `MULTI_SYMBOLS.context.md` — architecture de l'orchestrateur principal
- `state_manager.context.md` — format état JSON_V1 + HMAC-SHA256
- `walk_forward.context.md` — OOS gates + métriques anti-overfit

Consulter le fichier `.context.md` du module concerné avant toute modification.
