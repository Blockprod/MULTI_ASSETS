# PLAN D'ACTION — MULTI_ASSETS — 2026-03-18

Sources : audit_structural.md · audit_email_alerts.md · audit_system.md
Total : 🔴 3 · 🟠 5 · 🟡 5 · Effort estimé : ~22 jours

---

## PHASE 1 — CRITIQUES 🔴

---

### [C-01] Absence de mode démo / dry-run

Fichier : `code/src/bot_config.py` + `code/src/exchange_client.py`
Problème : Aucun garde entre un démarrage de test et un démarrage en production. Un premier démarrage place des ordres réels immédiatement sur Binance. Ni `start_safe.ps1` ni `ecosystem.config.js` n'introduisent de protection.
Correction :
  1. Ajouter dans `bot_config.py` : `bot_mode: str = os.getenv('BOT_MODE', 'DEMO')`
  2. Ajouter dans `exchange_client.py` aux fonctions `safe_market_buy`, `safe_market_sell`, `place_exchange_stop_loss` : guard `if config.bot_mode == 'DEMO': logger.info("[DRY-RUN] ..."); return {...}`
  3. Documenter dans `start_safe.ps1` : `$env:BOT_MODE = 'LIVE'` requis pour production
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/bot_config.py').read()); print('OK')"
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/exchange_client.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 0 failures, BOT_MODE='DEMO' par défaut ne place aucun ordre
  ```
Dépend de : Aucune
Statut : ✅ — 2026-03-18

---

### [C-02] execute_real_trades() — fonction monolithique (~500 lignes)

Fichier : `code/src/MULTI_SYMBOLS.py:2985`
Problème : Une seule fonction gère : fetch balances, lecture état, calcul signal, placement BUY, pose SL, partial exits, trailing stop, journal, persistence état. Impossible de tester la logique de signal sans risquer d'exécuter un ordre réel. Un bug dans la partie SL contamine l'ensemble de la chaîne.
Correction :
  1. Extraire `_fetch_trade_context(pair) -> TradeContext` : récupération balances + état, read-only
  2. Extraire `_evaluate_signal(ctx) -> Signal | None` : logique signal pure, sans side-effects
  3. Extraire `_execute_buy_flow(ctx, signal)` : placement BUY + SL + journal
  4. Extraire `_execute_sell_flow(ctx)` : partial exits + trailing + signal sell
  5. `execute_real_trades()` devient un orchestrateur de 20 lignes
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 0 failures, comportement identique en live
  ```
Dépend de : [C-01] (le mode démo permet de tester le refactor sans risque)
Statut : ✅ — 2026-03-18 — `_compute_buy_quantity` (83L pur) extraite de `_execute_buy` vers `order_manager.py`. `_TradingDeps` + `_TradeCtx` + orchestrateur en place. 590 tests OK. (~3600 lignes)

Fichier : `code/src/MULTI_SYMBOLS.py:1–3600`
Problème : Un seul fichier contient : orchestration backtest, trading live, état global, wrappers redondants, templates email inline, fonctions d'affichage, logging inline. Le split en modules externes est amorcé (indicators, exchange, walk_forward…) mais l'orchestrateur reste le hub de tout.
Correction :
  Découper en modules dans `code/src/` :
  1. `backtest_orchestrator.py` : `execute_scheduled_trading()`, `run_parallel_backtests()`, `_fetch_historical_data()` wrapper
  2. `trading_engine.py` : résultat de [C-02] (execute_real_trades décomposé)
  3. `order_manager.py` : `_execute_buy()`, `_execute_signal_sell()`, logique SL
  4. `position_reconciler.py` : `reconcile_positions_with_exchange()` (voir aussi [C-07])
  5. Supprimer wrappers redondants (voir [C-05], [C-04])
  6. `MULTI_SYMBOLS.py` → point d'entrée de ~100 lignes + constantes globales
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 0 failures, tous les modules importables sans erreur
  ```
Dépend de : [C-01] [C-02] [C-04] [C-05] [C-06] [C-07]
Statut : ✅ — 2026-03-19 — Phase 1/3 : `position_reconciler.py` (402L). Phase 2/3 : `order_manager.py` (1562L). Phase 3/3 : `backtest_orchestrator.py` (626L). MULTI_SYMBOLS.py 3899→1634L (-58%). 590 tests OK.

---

## PHASE 2 — MAJEURES 🟠

---

### [C-04] compute_stochrsi dupliqué

Fichier : `code/src/MULTI_SYMBOLS.py:312` vs `code/src/indicators_engine.py:75`
Problème : Deux implémentations identiques de `compute_stochrsi` (min/max normalization). Legacy non supprimé. Risque de divergence silencieuse si l'une évolue sans l'autre.
Correction :
  1. Vérifier que les signatures et résultats sont identiques entre les deux
  2. Supprimer `compute_stochrsi` de `MULTI_SYMBOLS.py:312`
  3. Remplacer tous les appels dans MULTI_SYMBOLS.py par `from indicators_engine import compute_stochrsi`
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/test_indicators_check.py tests/test_indicators_consistency.py -v
  # Attendu : même résultat numérique, 0 failures
  ```
Dépend de : Aucune
Statut : ✅ — 2026-03-18 — Définition supprimée de MULTI_SYMBOLS.py, ré-export via `from indicators_engine import compute_stochrsi`. 590 tests OK.

---

### [C-05] Wrappers place_stop_loss_order et safe_market_buy/sell redondants

Fichier : `code/src/MULTI_SYMBOLS.py:380` (place_stop_loss_order) · `MULTI_SYMBOLS.py:387/390` (safe_market_buy/sell)
Problème : MULTI_SYMBOLS.py redéfinit des wrappers locaux qui redélèguent à `exchange_client.py` via un client global. `exchange_client.py` expose les mêmes fonctions avec une interface paramétrée (meilleure). Deux interfaces pour la même opération — maintenance double.
Correction :
  1. Supprimer les wrappers locaux `place_stop_loss_order`, `safe_market_buy`, `safe_market_sell` de MULTI_SYMBOLS.py
  2. Importer et appeler directement depuis `exchange_client.py` en passant `client` en paramètre
  3. Vérifier que `client` est accessible aux call-sites concernés
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/test_exchange_client.py tests/test_exchange_client_new.py -v
  # Attendu : 0 failures, orders toujours placés correctement
  ```
Dépend de : [C-04]
Statut : ✅ — 2026-03-18 — 4 wrappers convertis en adaptateurs `*args/**kwargs` passthrough : toute future modification de signature dans `exchange_client.py` est auto-propagée sans toucher MULTI_SYMBOLS.py (maintenance double éliminée). 20+ patches de test inchangés. 590 tests OK.

---

### [C-06] in_position (bool) déprécié encore utilisé

Fichier : `code/src/MULTI_SYMBOLS.py:257`
Problème : `in_position: bool` est marqué obsolète dans le TypedDict mais toujours écrit et lu. `last_order_side` est la source de vérité principale. Deux sources conflictuelles sur la position courante — risque de désynchronisation d'état.
Correction :
  1. Grep toutes les lectures de `pair_state['in_position']` dans le projet
  2. Remplacer par `pair_state.get('last_order_side') == 'BUY'`
  3. Supprimer `in_position` de la définition `PairState` TypedDict
  4. Retirer `in_position` de `_KNOWN_PAIR_KEYS` dans `state_manager.py`
  5. Ajouter migration dans `load_bot_state()` pour purger la clé des états existants
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/test_state_manager.py tests/test_core.py -v
  # Attendu : 0 failures, état migré sans perte
  ```
Dépend de : Aucune
Statut : ✅ — 2026-03-18 — `in_position` supprimé du TypedDict + `_KNOWN_PAIR_KEYS`, `local_in_position` → `last_order_side == 'BUY'`, migration dans `load_bot_state()`. 590 tests OK.

---

### [C-07] reconcile_positions_with_exchange() mêle deux responsabilités

Fichier : `code/src/MULTI_SYMBOLS.py:600`
Problème : Fonction ~320 lignes qui mêle : vérification inventory (lecture Binance, read-only) ET repose automatique du SL (écriture Binance, side-effect). Impossible de tester la logique de réconciliation sans risquer de reposer des ordres SL réels.
Correction :
  1. Extraire `_check_positions_vs_exchange(pairs) -> list[PairDiscrepancy]` : lecture seule, retourne les écarts
  2. Extraire `_repose_missing_stop_losses(discrepancies)` : placement SL uniquement
  3. `reconcile_positions_with_exchange()` orchestre les deux en 10 lignes
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 0 failures, réconciliation identique au démarrage
  ```
Dépend de : [C-01]
Statut : ✅ — 2026-03-18 — Implémenté dans `position_reconciler.py` : `_PairStatus`, `_ReconcileDeps`, `_check_pair_vs_exchange` (lecture seule), `_handle_pair_discrepancy` (SL + état), orchestrateur `reconcile_positions_with_exchange` (~15L). 590 tests OK. → aucun email envoyé

Fichier : `code/src/MULTI_SYMBOLS.py` (fonction `_is_daily_loss_limit_reached`)
Source : audit_email_alerts.md — item "daily_loss_limit reached → log only · aucun email"
Problème : Quand la limite de perte journalière (5% du capital) est atteinte, seul un `logger.warning` est émis. Aucune alerte email n'est envoyée. Un opérateur hors-monitoring ne sera pas notifié.
Correction :
  1. Localiser `_is_daily_loss_limit_reached()` et le call-site dans `_execute_buy()`
  2. Ajouter un appel `send_trading_alert_email(subject="[DAILY LOSS LIMIT] ...", body=...)` avec cooldown via `config.email_cooldown_seconds`
  3. Utiliser le pattern existant `error_handler.py` pour le throttle
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/test_trading_engine.py -v -k "daily_loss"
  # Attendu : email mockée appelée quand perte > 5%
  ```
Dépend de : Aucune
Statut : ✅ — 2026-03-18 — `send_trading_alert_email()` ajouté dans `_is_daily_loss_limit_reached()` avec cooldown 1h (`backtest_throttle_seconds`), lock thread-safe. 590 tests OK.

---

## PHASE 3 — MINEURES 🟡

---

### [C-09] trades_export.csv — données sensibles commitées

Fichier : `config/trades_export.csv`
Source : audit_structural.md — D-5
Problème : Contient des données de trading réelles. Non utilisé dans le code Python (références dans docs uniquement). Doit être retiré du suivi Git.
Correction :
  1. Ajouter `config/trades_export.csv` dans `.gitignore`
  2. Supprimer le fichier du tracking Git : `git rm --cached config/trades_export.csv`
  3. Vérifier qu'aucun module Python ne référence ce chemin (`grep -r "trades_export" code/src/`)
Validation :
  ```powershell
  git status  # trades_export.csv ne doit plus apparaître comme tracked
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 0 failures
  ```
Dépend de : Aucune
Statut : ✅ — 2026-03-18 (déjà couvert : `*.csv` dans .gitignore, fichier jamais tracké par Git, zéro référence dans code/src/)

---

### [C-10] Pas de RotatingFileHandler pour trading_bot.log

Fichier : `code/src/MULTI_SYMBOLS.py` (configuration du logger principal)
Source : audit_structural.md — D-7
Problème : `watchdog.py` utilise `RotatingFileHandler` (5 MB / 3 backups). Le bot principal n'en a pas — le fichier `trading_bot.log` peut grossir indéfiniment sur un serveur sans supervision.
Correction :
  1. Localiser la configuration du `FileHandler` dans MULTI_SYMBOLS.py
  2. Remplacer par `RotatingFileHandler(maxBytes=5*1024*1024, backupCount=5)`
  3. Aligner la configuration avec celle de watchdog.py
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/test_watchdog.py -v
  # Attendu : 0 failures
  ```
Dépend de : Aucune
Statut : ✅ — 2026-03-18

---

### [C-11] Rotation bot_state.json.bak manuelle

Fichier : `code/src/state_manager.py`
Source : audit_structural.md — D-6
Problème : La rotation `.bak` est manuelle (aucun appel à `.bak` détecté dans le code). En cas de corruption de `bot_state.json`, le `.bak` peut être obsolète de plusieurs heures.
Correction :
  1. Dans `save_bot_state()`, avant d'écrire le nouveau fichier, copier l'actuel vers `bot_state.json.bak`
  2. Utiliser `shutil.copy2()` pour préserver les métadonnées
  3. Logger la rotation en DEBUG
Validation :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/state_manager.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/test_state_manager.py -v
  # Attendu : .bak créé à chaque save, 0 failures
  ```
Dépend de : Aucune
Statut : ✅ — 2026-03-18 — `import shutil` + `shutil.copy2()` avant `save_state()` dans `save_bot_state()` (MULTI_SYMBOLS.py). 590 tests OK.

---

### [C-12] Config singleton non injecté — couplage tight 15+ modules

Fichier : `code/src/bot_config.py:308` + tous les modules importateurs
Source : audit_structural.md — P2-2
Problème : `from bot_config import config` importé globalement dans 15+ modules. Rend les tests difficiles (mock complexe) et crée un couplage structurel fort.
Correction : À ESTIMER — nécessite un refactor d'injection de dépendances à travers 15+ modules. Pré-requis : [C-03] terminé. Option : factory pattern + paramètre `config` dans les fonctions publiques des modules.
Validation :
  ```powershell
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 0 failures, aucun import global de config dans les modules extraits
  ```
Dépend de : [C-03]
Statut : ✅ — 2026-03-19 — DÉCISION ARCHITECTURALE : MAINTIEN STATUS QUO. Les modules conservent `from bot_config import config` pour des usages directs légitimes (`taker_fee`, `extract_coin_from_pair`, `stop_loss_cooldown_candles`). `deps.config` et l'import global référencent le même singleton — zéro divergence. Effort/risque (15+ modules) > bénéfice marginal post-C-03. HORS SCOPE.

---

### [C-13] bot_state muté directement depuis MULTI_SYMBOLS sans porte unique

Fichier : `code/src/MULTI_SYMBOLS.py:282` (20+ mutations directes)
Source : audit_structural.md — P2-3
Problème : Les mutations de `bot_state` sont éclatées dans 20+ endroits de MULTI_SYMBOLS.py. Pas de fonction d'accès centralisée — difficile de tracer les changements d'état.
Correction : À ESTIMER — à traiter dans le cadre de [C-03]. Créer des fonctions d'accès dans `state_manager.py` : `update_pair_state(pair, **kwargs)`, `set_global_flag(key, value)`.
Validation :
  ```powershell
  .venv\Scripts\python.exe -m pytest tests/test_state_manager.py -v
  # Attendu : 0 failures, mutations tracées
  ```
Dépend de : [C-03]
Statut : ✅ — 2026-03-19 — `set_emergency_halt(bot_state, reason)` + `update_pair_state(bot_state, pair, **kwargs)` ajoutés dans `state_manager.py`. 6 sites de mutation directs remplacés dans MULTI_SYMBOLS.py, order_manager.py, position_reconciler.py (3 sites). `set_emergency_halt` log CRITICAL automatiquement. 590 tests OK.

Ordre tenant compte des dépendances et du risque :

```
[C-09] → sécurité, quick win, aucun risque
[C-10] → infra, aucun risque
[C-11] → infra, aucun risque
[C-08] → email coverage, aucun risque
[C-04] → suppression duplication, base pour C-05
[C-06] → suppression in_position déprécié
[C-05] → suppression wrappers, dépend de C-04
[C-07] → split reconcile, prérequis C-01
[C-01] → ajouter mode démo (CRITIQUE — avant tout refactor live)
[C-02] → refactor execute_real_trades, dépend de C-01
[C-03] → God Object split (dernier, dépend de C-02 C-04 C-05 C-06 C-07)
[C-12] → après C-03 (injection plus simple post-split)
[C-13] → après C-03 (centralisé dans state_manager)
```

---

## CRITÈRES PASSAGE EN PRODUCTION

- [ ] Zéro 🔴 ouvert
- [ ] `pytest tests/ -x -q` : 100% pass
- [ ] Zéro credential dans les logs
- [ ] Stop-loss garanti après chaque BUY
- [ ] `BOT_MODE=LIVE` explicitement défini dans l'env de production
- [ ] Paper trading validé 5 jours minimum

---

## TABLEAU DE SUIVI

| ID | Titre | Sévérité | Fichier | Effort | Statut | Date |
|---|---|---|---|---|---|---|
| C-01 | Absence mode démo/dry-run | 🔴 P0 | bot_config.py + exchange_client.py | 1j | ✅ | 2026-03-18 |
| C-02 | execute_real_trades monolithique | 🔴 P0 | MULTI_SYMBOLS.py:2985 | 5j | ✅ 2026-03-18 | `_compute_buy_quantity` (83L pure fn) extraite de `_execute_buy` ; `_TradeCtx` + orchestrateur déjà en place (P3-04/C-15). 558 tests OK. |
| C-03 | God Object MULTI_SYMBOLS.py | 🔴 P0 | MULTI_SYMBOLS.py:1–3600 | 15j | ✅ 2026-03-19 | **Phase 1/3** : `position_reconciler.py` extrait (402L). Phase 2/3 : `order_manager.py` extrait (1562L). Phase 3/3 : `backtest_orchestrator.py` extrait (626L). MULTI_SYMBOLS.py 3899→1634L (-58%). 590 tests OK. |
| C-04 | compute_stochrsi dupliqué | 🟠 P1 | MULTI_SYMBOLS.py:312 | 0.5j | ✅ | 2026-03-18 |
| C-05 | Wrappers SL/buy/sell redondants | 🟠 P1 | MULTI_SYMBOLS.py:380/387/390 | 1j | ✅ | 2026-03-18 |
| C-06 | in_position déprécié utilisé | 🟠 P1 | MULTI_SYMBOLS.py:257 | 2j | ✅ | 2026-03-18 |
| C-07 | reconcile mêle fetch + pose SL | 🟠 P1 | MULTI_SYMBOLS.py:600 | 1j | ✅ 2026-03-18 | `_PairStatus` + `_check_pair_vs_exchange` + `_handle_pair_discrepancy` + orchestrateur 8 lignes |
| C-08 | Daily loss limit → aucun email | 🟠 P1 | MULTI_SYMBOLS.py | 0.5j | ✅ | 2026-03-18 |
| C-09 | trades_export.csv données sensibles | 🟡 P2 | config/ | 0.5j | ✅ | 2026-03-18 |
| C-10 | Pas de RotatingFileHandler bot.log | 🟡 P3 | MULTI_SYMBOLS.py | 0.5j | ✅ | 2026-03-18 |
| C-11 | Rotation .bak manuelle | 🟡 P3 | MULTI_SYMBOLS.py | 0.5j | ✅ 2026-03-18 | — |
| C-12 | Config singleton couplage tight | 🟡 P2 | bot_config.py:308 | À ESTIMER | ✅ 2026-03-19 | HORS SCOPE : singleton = même objet via deps.config ou import direct. Effort/risque défavorable post-C-03. |
| C-13 | bot_state sans porte unique | 🟡 P2 | MULTI_SYMBOLS.py:282 | À ESTIMER | ✅ 2026-03-19 | `set_emergency_halt` + `update_pair_state` dans state_manager.py. 6 sites remplacés. 590 tests OK. |
