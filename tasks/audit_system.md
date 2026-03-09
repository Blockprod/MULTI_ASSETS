# Audit System — MULTI_ASSETS

> **Statut** : 🔄 Checklist permanente — à parcourir avant chaque release production. Ne pas modifier sans validation préalable.

*Template de session d'audit. Cocher chaque item avant de déclarer "produit ready".*

## BLOC 1 — Biais Backtest
- [ ] `start_date` calculée avec `_fresh_start_date()` à chaque cycle (pas de variable figée)
- [ ] Indicateurs calculés avec `shift(1)` sur les bougies 4h pour le MTF filter
- [ ] StochRSI utilise `iloc[-2]` (bougie fermée) pas `iloc[-1]` (bougie en cours)
- [ ] Fees backtest (`backtest_taker_fee`) jamais écrasées par `get_binance_trading_fees()`
- [ ] `MIN_NOTIONAL=5 USDC` simulé dans le backtest (filtre LOT_SIZE respecté)
- [ ] Walk-forward ancré (anchored, pas rolling) avec au minimum 3 folds
- [ ] OOS gates actifs : Sharpe≥0.8, WinRate≥30%, decay ratio≥0.15
- [ ] `WF_SCENARIOS` est la source unique (pas de liste inline dupliquée)

## BLOC 2 — Thread Safety
- [ ] Toute écriture dans `bot_state` dans un bloc `with _bot_state_lock:`
- [ ] Pas de lecture-modification-écriture non atomique sur `bot_state`
- [ ] `_pair_execution_locks[pair]` acquis avant chaque `monitor_and_trade_for_pair()`
- [ ] `_oos_alert_lock` présent autour de `_oos_alert_last_sent` (lecture ET écriture)
- [ ] `indicators_cache` accès protégé par `_indicators_cache_lock`
- [ ] `_exchange_info_cache` dans `data_fetcher.py` : accès concurrents sûrs ?
- [ ] `save_bot_state()` thread-safe via `_bot_state_lock` ✓

## BLOC 3 — Sécurité Credentials
- [ ] `Config.__repr__` masque `api_key` et `secret_key` (C-10)
- [ ] Aucun `print(config)` ou `logger.info(f"config: {config}")` en clair
- [ ] `BINANCE_SECRET_KEY` utilisé uniquement comme clé HMAC (pas loggé)
- [ ] Pas de credentials dans les fichiers de cache ou l'état (bot_state.json)
- [ ] Emails d'alerte ne contiennent pas de clés API

## BLOC 4 — Robustesse Exchange
- [ ] `safe_market_buy/sell` : idempotence via `origClientOrderId` avant retry
- [ ] `place_exchange_stop_loss` : lève `OrderError` au lieu de retourner `None` si échec
- [ ] Après BUY raté : `pair_state['in_position']` reste False
- [ ] Après BUY réussi + SL raté : `safe_market_sell` de clôture déclenché
- [ ] `get_spot_balance_usdc` lève `BalanceUnavailableError` (pas de "balance=0" silencieux)
- [ ] `reconcile_positions_with_exchange()` appelé au démarrage après `load_bot_state()`
- [ ] C-11 : repose automatique du SL si position ouverte sans stop sur Binance
- [ ] Annulation du SL exchange (F-1) avant vente partielle/signal pour débloquer les coins

## BLOC 5 — Intégrité de l'état
- [ ] `bot_state.json` au format `JSON_V1:` + HMAC-SHA256
- [ ] `StateError` levée sur corruption → démarrage avec état vide + alerte email
- [ ] Nouvelles clés PairState dans `_KNOWN_PAIR_KEYS` (state_manager.py)
- [ ] Nouvelles clés globales dans `_KNOWN_GLOBAL_KEYS`
- [ ] `save_bot_state(force=True)` après chaque changement critique (achat, vente, SL)
- [ ] 3 échecs consécutifs de save → `emergency_halt = True`

## BLOC 6 — Protection du Capital
- [ ] `_is_daily_loss_limit_reached()` appelé avant chaque achat dans `_execute_buy()`
- [ ] `_update_daily_pnl()` appelé après chaque vente (stop-loss ET signal)
- [ ] `emergency_halt` vérifié en début de chaque cycle dans la boucle principale
- [ ] `oos_blocked` préservé au redémarrage (C-05 : pas purgé par `load_bot_state`)
- [ ] `partial_taken_1/2` réinitialisés uniquement après une vente totale

## BLOC 7 — Gestion d'erreurs
- [ ] Zéro `except Exception: pass` dans le code de production
- [ ] `log_exceptions` decorator sur les fonctions non-critiques (retour `default_return`)
- [ ] Fonctions critiques (SL, état) avec gestion explicite, pas de decorator silencieux
- [ ] Emails d'alerte avec cooldown `config.email_cooldown_seconds = 300s`
- [ ] Watchdog : heartbeat.json fraîcheur < 600s avant considérer le bot comme hung

## BLOC 8 — Tests
- [ ] `pytest tests/ -x -q` passe en vert (0 failures)
- [ ] Tests de corruption dans `TestCorruptionRobustness` (6 tests)
- [ ] Mocks API dans tous les tests exchange (pas d'appel réseau réel)
- [ ] Tests de sizing couvrent : ATR=0, entry_price=0, equity=0
- [ ] Test d'idempotence de `safe_market_buy` (ordre déjà exécuté avant retry)
