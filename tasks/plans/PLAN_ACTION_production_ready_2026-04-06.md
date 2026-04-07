# PLAN D'ACTION — Production-Ready Upgrade
**Creation :** 2026-04-06 à 13:00  
**Source :** Audit critique approfondi du 2026-04-06 (score 7.0/10 — verdict SOLIDE)  
**Objectif :** Faire passer le système de SOLIDE → PRODUCTION-READY  
**Baseline :** 689 tests, Ruff clean, ~10 900 LOC prod / ~11 300 LOC tests

---

## Synthèse des faiblesses identifiées

| # | Faiblesse | Risque prod | Phase |
|---|-----------|-------------|-------|
| F-01 | God Module MULTI_SYMBOLS.py (2 650 LOC, 15+ globals) | Régressions, revue impossible | P1 |
| F-02 | Fonctions géantes (_execute_buy ~400L, _check_and_execute_stop_loss ~350L) | Bug masqués, cyclomatique élevée | P0 |
| F-03 | Config.taker_fee mutée au runtime | Race condition threads, fees backtest corrompues | P0 |
| F-04 | Silent failures (fetch_balances, dust cleanup → return None) | Phantom state, ordres sous-dimensionnés | P0 |
| F-05 | Aucune intégrité Cython (.pyd non vérifié au boot) | Fallback Python silencieux, backtest 50x plus lent | P1 |
| F-06 | Gmail SMTP seul provider email | Alertes critiques perdues si Google rate-limit | P2 |
| F-07 | Pas de log rotation (trade journal, trading_bot.log) | Disk full → crash silencieux | P1 |
| F-08 | Magic numbers hardcodés (0.20, 1.02, 3600, etc.) | Ajustements = toucher au code business | P1 |
| F-09 | Pas d'abstraction exchange (couplage direct Binance) | Impossible d'ajouter un 2e exchange | P2 |
| F-10 | Backtest sans slippage stochastique | Résultats optimistes ~5-15 bps | P2 |
| F-11 | Pas de test d'intégration E2E (Binance testnet) | Confiance limitée sur la chaîne BUY→SL→SELL réelle | P2 |

---

## Phase 0 — CRITIQUE (risque capital immédiat)

> Objectif : éliminer les race conditions et les failures silencieuses.
> Estimation : 4 items, tests verts obligatoires après chaque item.

### P0-01 · Geler Config post-init ✅

**Problème :** `config.taker_fee` peut être overwritten au runtime → race condition entre threads scheduler et trade execution. Backtest peut accidentellement utiliser des fees live.

**Réalisé :**
- [x] Ajout `__setattr__` override sur `Config` — lève `AttributeError` dès `_frozen = True`
- [x] `object.__setattr__(self, '_frozen', True)` en fin de `_validate()` dans `from_env()`
- [x] MULTI_SYMBOLS.py : vars `_runtime_taker_fee` / `_runtime_maker_fee` remplacent la mutation config
- [x] `cache_manager.py` : `_effective_cache_dir` + `_get_cache_dir()` — plus de mutation config
- [x] Tests `test_email_alert.py` corrigés (monkeypatch module, pas attribut singleton)
- [x] Tests `test_cache_manager.py` corrigés + reset `_effective_cache_dir` dans fixture
- [x] 4 nouveaux tests `TestConfigFrozen` dans `test_core.py`
- [x] **689 tests ✅ · Ruff clean**

---

### P0-02 · Éliminer les silent failures critiques ✅

**Problème :** `_fetch_balances()` retourne `None` silencieusement. Le bot continue de trader sans balance fraîche → ordres sous-dimensionnés ou dépassement de capacité.

**Action :**
- [x] `_fetch_balances()` : lever `BalanceUnavailableError` si `client.get_account()` échoue
- [x] `_execute_real_trades_inner()` : log CRITICAL si `_fetch_balances()` retourne None avec position BUY ouverte
- [x] `_execute_real_trades_inner()` : log CRITICAL si `_fetch_symbol_filters()` retourne None avec position BUY ouverte
- [x] `state_manager.py` : ajout `_effective_states_dir` / `_effective_state_file` + `_get_state_path()` (correction silent ConfiError de P0-01)
- [x] Tests : `TestFetchBalancesSilentFailure` (3 tests) + `TestExecuteTradesCriticalLogs` (3 tests) dans `test_p0_fixes.py`
- [x] Tests `test_state_manager.py` : fixture `tmp_state_dir` migrée vers les vars runtime

**Fichiers modifiés :** `MULTI_SYMBOLS.py`, `state_manager.py`, `tests/test_p0_fixes.py`, `tests/test_state_manager.py`  
**Validation :** 270 tests passent (`test_p0_fixes`, `test_state_manager`, `test_core`, `test_cache_manager`, `test_email_alert`, `test_execute_trades_unit`, `test_trading_engine`) · Ruff clean

---

### P0-03 · Découper _execute_buy() en sous-fonctions ✅

**Problème :** ~400 lignes, if-nesting 5+ niveaux. Impossible à reviewer, cyclomatique complexité élevée. Bug masqués.

**Action :**
- [x] Extraire : `_validate_buy_preconditions(ctx) → bool`
- [x] Extraire : `_compute_and_validate_quantity(ctx, deps) → Decimal`
- [x] Extraire : `_place_buy_and_verify(ctx, deps, qty) → dict`
- [x] Extraire : `_place_sl_after_buy(ctx, deps, fill_result) → bool`
- [x] Extraire : `_rollback_on_sl_failure(ctx, deps) → None`
- [x] `_execute_buy()` devient un orchestrateur de 50 lignes max
- [x] Tests existants doivent passer sans modification (refactoring pur)

**Fichiers :** `order_manager.py`, `tests/test_phase1_fixes.py` (fix test frozen Config P0-01)  
**Validation :** 699 tests passent · Syntax OK

---

### P0-04 · Découper _check_and_execute_stop_loss() ✅

**Problème :** ~350 lignes, logique SL dupliquée 3x (manuel, trailing, exchange-filled).

**Action :**
- [x] Extraire : `_handle_manual_sl_trigger(ctx, deps) → bool`
- [x] Extraire : `_handle_exchange_sl_fill(ctx, deps) → bool`
- [x] `_update_trailing_stop` existait déjà (ligne 194) — item couvert
- [x] Fonction principale réduite à un dispatcher de ~25 lignes
- [x] Tests existants inchangés

**Fichiers :** `order_manager.py`  
**Validation :** 699 tests passent · Syntax OK

---

## Phase 1 — IMPORTANT (stabilité long terme)

> Objectif : résilience opérationnelle, observabilité, maintenabilité.
> Dépend de : Phase 0 terminée.

### P1-01 · Intégrité Cython au boot ✅

**Problème :** Si le `.pyd` est corrompu, fallback Python silencieux → backtest 50x plus lent, timeout OOS, pas d'alerte.

**Action :**
- [x] Calculer SHA256 des `.pyd` au build, stocker dans `code/bin/checksums.json`
- [x] Nouveau module `code/src/cython_integrity.py` : `verify_cython_integrity(alert_fn)` + `generate_checksums()`
- [x] Au boot (`MULTI_SYMBOLS.py`), appeler `_verify_cython_integrity(alert_fn=send_email_alert)` après les logs Cython
- [x] Flag `CYTHON_INTEGRITY_VERIFIED` exporté, consultable par le watchdog
- [x] 8 tests : `TestVerifyCythonIntegrity` (6) + `TestGenerateChecksums` (2)

**Fichiers :** `code/src/cython_integrity.py` (nouveau), `code/bin/checksums.json` (nouveau), `MULTI_SYMBOLS.py`, `tests/test_cython_integrity.py` (nouveau)  
**Validation :** 716 tests passent

---

### P1-02 · Log rotation ✅

**Problème :** `trading_bot.log` et trade journal JSONL grandissent sans limite → disk full silencieux.

**Action :**
- [x] `trading_bot.log` : RotatingFileHandler(5MB, 5 backups) — déjà en place dans MULTI_SYMBOLS.py et watchdog.py
- [x] `trade_journal.py` : rotation mensuelle lazy (rename `trade_journal.jsonl` → `journal_YYYY-MM.jsonl` au premier write d'un nouveau mois)
- [x] Watchdog : surveille l'espace disque (`shutil.disk_usage()`), log CRITICAL + email si < 500 MB
- [x] Tests : `TestMonthlyRotation` (5 tests), `TestDiskSpaceCheck` (4 tests)

**Fichiers :** `trade_journal.py`, `watchdog.py`, `tests/test_trade_journal.py`, `tests/test_watchdog.py`  
**Validation :** 708 tests passent

---

### P1-03 · Centraliser les magic numbers ✅

**Problème :** Constantes hardcodées dans le code business (0.20 dust, 1.02 tolerance, 3600s cooldowns, partiels 0.45-0.55).

**Action :**
- [x] Créer `code/src/constants.py` regroupant toutes les constantes nommées
- [x] Migrer depuis `order_manager.py` : `DUST_FINAL_FRACTION`, `QTY_OVERSHOOT_TOLERANCE`, `SL_RETRY_COUNT`, `SL_BACKOFF_BASE`, `TIMEFRAME_SECONDS` (3 dicts dupliqués)
- [x] Migrer depuis `trade_helpers.py` : `PARTIAL_1/2_PROFIT_PCT`, `PARTIAL_1/2_QTY_MIN/MAX`, `SNIPER_BAND_PCT`
- [x] Migrer depuis `MULTI_SYMBOLS.py` : `SAVE_THROTTLE_SECONDS`, `MAX_SAVE_FAILURES`
- [x] Tests inchangés (même valeurs, juste centralisées)

**Fichiers :** `constants.py` (nouveau), `order_manager.py`, `trade_helpers.py`, `MULTI_SYMBOLS.py`  
**Validation :** 708 tests passent

---

### P1-04 · Réduire MULTI_SYMBOLS.py (extract globals) ✅

**Problème :** 15+ dicts globaux module-level (`_last_backtest_time`, `_live_best_params`, `_oos_alert_last_sent`, etc.)

**Action :**
- [x] Créer une class `_BotRuntime` regroupant tous les états runtime (caches, timestamps, throttles, fees)
- [x] Instancier un seul `_runtime = _BotRuntime()` au niveau module
- [x] Remplacer les accès `_last_backtest_time[pair]` par `_runtime.last_backtest_time[pair]`
- [x] Aucun changement de logique — refactoring pur
- [x] MULTI_SYMBOLS.py était déjà à 1787 lignes (< 2000) après les extractions P0

**Fichiers :** `MULTI_SYMBOLS.py`, `tests/test_phase1_fixes.py`, `tests/test_execute_trades_unit.py`, `tests/test_trading_engine.py`  
**Validation :** 725 tests passent

---

### P1-05 · Alertes email throttle global + fallback ✅

**Problème :** Cooldown emails dupliqué dans 4+ modules avec des locks séparés. Pas de fallback si Gmail down.

**Action :**
- [x] Centraliser dans `error_handler.py` : un seul `AlertThrottle` class (cooldown configurable)
- [x] Remplacer les 4 locks d'alerte de MULTI_SYMBOLS.py par des appels à `AlertThrottle`
- [x] Ajouter un fallback : écriture dans un fichier `alerts_unsent.jsonl` si SMTP fail
- [x] Test : `TestAlertThrottle` (6 tests) + `TestEmailFallback` (3 tests)

**Fichiers :** `error_handler.py`, `MULTI_SYMBOLS.py`, `email_utils.py`  
**Validation :** 725 tests passent

---

## Phase 2 — AMÉLIORATION (scalabilité, réalisme)

> Objectif : préparer l'avenir sans casser le présent.
> Dépend de : Phase 1 terminée.

### P2-01 · Interface exchange abstraite ✅

**Problème :** Couplage direct à `BinanceFinalClient` partout → impossible d'ajouter un 2e exchange.

**Action :**
- [x] Définir `ExchangePort` Protocol (15 méthodes structurelles couvrant tous les usages deps.client)
- [x] `BinanceFinalClient` implémente `ExchangePort` structurellement via python-binance Client
- [x] Les DI deps (`_TradingDeps`, `_ReconcileDeps`, `_BacktestDeps`) typent `client: ExchangePort`
- [x] Aucun changement fonctionnel — refactoring de type uniquement
- [x] Tests existants passent sans modification

**Fichiers :** `exchange_client.py`, `order_manager.py`, `position_reconciler.py`, `backtest_orchestrator.py`  
**Validation :** 725 tests passent + pyright 0 errors, 0 warnings

---

### P2-02 · Slippage stochastique dans le backtest ✅

**Problème :** Backtest utilise des fees fixes → résultats optimistes ~5-15 bps.

**Action :**
- [x] Ajouter `slippage_model: Optional[BasicSlippageModel] = None` à `backtest_from_dataframe()`
- [x] Implémenter `BasicSlippageModel` : spread random 1-3 bps + volume impact factor (percentile roulant 50 bars)
- [x] Activé uniquement en mode validation OOS (`run_walk_forward_validation`) — pas en grid search
- [x] Tests : `TestBasicSlippageModel` (4 tests) + `TestBacktestWithSlippage` (3 tests dont `test_backtest_with_slippage_returns_lower_sharpe`)

**Fichiers :** `backtest_runner.py`, `walk_forward.py`, `tests/test_backtest.py`  
**Validation :** 731 tests passent (6 nouveaux), 1 skipped

---

### P2-03 · Tests E2E sur Binance Testnet ✅

**Problème :** Aucun test sur l'API réelle. Confiance limitée sur la chaîne BUY→SL→SELL.

**Action :**
- [x] Ajouter `tests/test_e2e_testnet.py` (marqué `@pytest.mark.testnet`, skip par défaut)
- [x] Scénarios : TestTestnetConnectivity (3 tests) + TestBuySLSellChain (2 tests) + TestIdempotence (1 test)
- [x] Utiliser Binance Testnet API keys (env vars `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_SECRET_KEY`)
- [x] Marqueur `testnet` enregistré dans `pyproject.toml`
- [x] Skip automatique si clés absentes — 6 tests skippés dans la CI normale

**Fichiers :** `tests/test_e2e_testnet.py` (nouveau), `pyproject.toml`  
**Validation :** `pytest tests/test_e2e_testnet.py -m testnet` (manuel avec clés testnet)

---

### P2-04 · Monitoring & métriques ✅

**Problème :** Observabilité limitée aux logs et emails. Pas de métriques exploitables.

**Action :**
- [x] Créer `code/src/metrics.py` avec `write_metrics()` et `read_metrics()`
- [x] Écriture atomique (write-then-rename) dans `metrics/metrics.json` toutes les 5 minutes
- [x] Métriques : positions ouvertes, PnL entrée, oos_blocked, drawdown_halted, sl_placed, save_failure_count, circuit_breaker_available, taker/maker fee, latence API
- [x] Job scheduler `_periodic_metrics_write()` enregistré dans MULTI_SYMBOLS.py
- [x] Tests : `TestWriteMetrics` (6 tests) + `TestReadMetrics` (2 tests)

**Fichiers :** `code/src/metrics.py` (nouveau), `MULTI_SYMBOLS.py`, `tests/test_metrics.py` (nouveau)  
**Validation :** 739 tests passent (8 nouveaux), 7 skipped

---

## Ordre d'exécution recommandé

```
Phase 0 (critique — faire en premier, dans cet ordre) :
  P0-01 → P0-02 → P0-03 → P0-04

Phase 1 (stabilité — après Phase 0 validée) :
  P1-02 → P1-03 → P1-01 → P1-05 → P1-04

Phase 2 (améliorations — quand Phase 1 stable) :
  P2-01 → P2-02 → P2-03 → P2-04
```

---

## Critères de succès (Production-Ready)

| Critère | Seuil |
|---------|-------|
| Tous les tests passent | 689+ tests, 0 failures |
| Ruff clean | 0 erreurs |
| Aucune fonction > 80 lignes dans order_manager.py | Cyclomatique < 15 |
| Config immutable post-init | `test_config_frozen_after_init` passe |
| Aucun silent failure critique | Toutes les erreurs API propagées ou retried |
| Log rotation active | `RotatingFileHandler` configuré |
| Intégrité Cython vérifiée au boot | Checksum SHA256 validé |
| Magic numbers centralisés | `constants.py` créé, grep propre |
| Score audit ≥ 8.5/10 | Re-audit post Phase 1 |

---

## Risques du plan

| Risque | Mitigation |
|--------|------------|
| Régression sur order_manager (P0-03/04) | Extraction progressive, 1 fonction à la fois, tests après chaque extraction |
| Config frozen casse un module qui mute | Grep exhaustif `config\.\w+ =` avant freeze, fix tous les mutateurs |
| MULTI_SYMBOLS.py refacto trop large (P1-04) | Faire en dernière Phase 1, quand le reste est stable |
| Slippage model ralentit le backtest (P2-02) | Activer uniquement sur OOS validation, pas sur grid search |
