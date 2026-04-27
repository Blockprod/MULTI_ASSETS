# MULTI_ASSETS — Leçons apprises (Self-Improvement Loop)

> **Lire ce fichier au début de chaque session.**  
> Mettre à jour après toute correction de l'utilisateur.  
> Chaque entrée = un pattern d'erreur à ne plus reproduire.

**Sévérités** : 🔴 CRITIQUE (risque capital) · 🟡 IMPORTANT (bug silencieux / CI) · 🔵 INFO (outillage)

---

## Règles Exchange & Capital

### L-01 · `TRAILING_STOP_MARKET` généré sur Binance Spot
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-03-01

**Contexte** : Agent IA génère du code utilisant `TRAILING_STOP_MARKET` (ex: ordre de stop dynamique).  
**Erreur** : Ce type d'ordre n'existe pas sur Binance Spot → lève `NotImplementedError` au runtime.  
**Règle** : Stop-loss = `STOP_LOSS_LIMIT` uniquement, posé immédiatement après chaque BUY.  
**Ref** : `.claude/rules.md` interdiction #5

---

### L-02 · `start_date` figée à l'import
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-03-01

**Contexte** : Agent initialise `start_date = datetime.now() - timedelta(days=1095)` hors d'une fonction.  
**Erreur** : La date se fige au moment de l'import du module → biais temporel croissant au fil du temps.  
**Règle** : Toujours utiliser `_fresh_start_date()` — calculé dynamiquement à chaque appel.  
**Ref** : `.claude/rules.md` interdiction #4

---

## Thread Safety & État persisté

### L-03 · Écriture dans `bot_state` sans `_bot_state_lock`
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-03-01

**Contexte** : Agent ajoute ou modifie une clé dans `bot_state[pair]` directement, hors verrou.  
**Erreur** : Race condition possible avec le thread scheduler (toutes les 2 minutes par paire).  
**Règle** : Toute écriture dans `bot_state` doit être encapsulée dans `with _bot_state_lock:`.  
**Pattern correct** :
```python
with _bot_state_lock:
    bot_state[pair]["some_key"] = value
    save_bot_state()
```
**Ref** : `.claude/rules.md` interdiction #6

---

## Outillage & CI

### L-04 · Pyright `typeCheckingMode: "basic"` → 91 faux positifs Pandas en CI
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-03-18

**Contexte** : Tentative d'activation de `typeCheckingMode: "basic"` dans `pyrightconfig.json`.  
**Erreur** : Les stubs Pandas génèrent 91 erreurs (reportArgumentType, reportAttributeAccessIssue, reportReturnType) — tous faux positifs sur des subscripts `df["col"]`.  
**Règle** : Garder `typeCheckingMode: "off"` avec `reportMissingImports: "error"` et `reportUndefinedVariable: "error"` explicites. Ne pas reverter vers `"basic"`.  
**Ref** : `pyrightconfig.json` — commit `4b23841`

---

### L-05 · Versions Python contradictoires entre fichiers
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-03-18

**Contexte** : Modification d'un seul fichier mentionnant la version Python (README, CI, requirements).  
**Erreur** : Divergence entre les sources (copilot-instructions, main.yml, requirements.txt, README, pyproject.toml).  
**Règle** : Source de vérité = `pyproject.toml` (`requires-python = ">=3.11"`). Version réelle du venv : **Python 3.11.9**. Mettre à jour tous les fichiers simultanément. Ne jamais supposer une version sans vérifier `.venv/Scripts/python.exe --version`.  
**Ref** : `docs/STRUCTURAL_AUDIT_ACTION_PLAN.md` — P0-2

---

### L-06 · `pytest.ini` a priorité sur `pyproject.toml`
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-03-18

**Contexte** : Les deux fichiers coexistent avec des sections de configuration pytest.  
**Erreur** : `pytest.ini` prend la priorité → `[tool.pytest.ini_options]` dans `pyproject.toml` silencieusement ignoré.  
**Règle** : Un seul fichier de config pytest — `pyproject.toml`. `pytest.ini` a été supprimé (commit `8cc5c0a`). Ne jamais recréer `pytest.ini`.  
**Ref** : `docs/STRUCTURAL_AUDIT_ACTION_PLAN.md` — P1-3

---

### L-07 · `python.analysis.extraPaths` en conflit avec `pyrightconfig.json`
**Sévérité** : 🔵 INFO · **Date** : 2026-03-18

**Contexte** : `python.analysis.extraPaths` défini dans `.vscode/settings.json` alors que `pyrightconfig.json` existe à la racine.  
**Erreur** : Pylance affiche un warning et ignore le paramètre — résolution des imports non garantie.  
**Règle** : Quand `pyrightconfig.json` est présent, toute la config des chemins (`extraPaths`, `stubPath`) doit être dans `pyrightconfig.json` uniquement. Supprimer `python.analysis.extraPaths` de `settings.json`.  
**Ref** : commit `4b23841`

---

### L-08 · Supposer une version Python sans vérifier le venv
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-03-18

**Contexte** : Décision de "version Python unifiée" prise sans vérifier `.venv/Scripts/python.exe --version`.  
**Erreur** : Version 3.13 propagée partout alors que le venv local tourne en **3.11.9**. Le `.cp311.pyd` est le binaire actif — modifier le CI vers 3.13 aurait cassé la production.  
**Règle** : Toujours exécuter `.venv\Scripts\python.exe --version` avant toute décision de version. Les `.pyd` dans `code/bin/` indiquent les versions actives (`.cp311` = 3.11, `.cp313` = 3.13).  
**Action corrective** : Revert README, requirements.txt, main.yml, pyproject.toml, copilot-instructions.md. Recompiler avec `config/setup.py build_ext --inplace --force`, copier dans `code/bin/`.

---

### L-09 · PowerShell `Set-Content -Encoding UTF8` écrit un BOM UTF-8
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-03-18

**Contexte** : Tentative de modifier un fichier `.py` via PowerShell `Set-Content ... -Encoding UTF8`.  
**Erreur** : PowerShell 5.1 écrit systématiquement un BOM (`\xEF\xBB\xBF`) → Python interprète le BOM comme faisant partie du premier token, corrompant la syntaxe.  
**Règle** : Ne **jamais** utiliser `Set-Content` pour des fichiers `.py` ou `.md`. Utiliser exclusivement `replace_string_in_file` ou `open(..., 'w', encoding='utf-8')`.  
**Pattern si PowerShell obligatoire** :
```powershell
[System.IO.File]::WriteAllText($path, $content, [System.Text.UTF8Encoding]::new($false))
```

---

### L-10 · `git show HEAD:path > fichier` écrase avec l'erreur git si le fichier n'est pas tracké
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-03-18

**Contexte** : Tentative de restaurer un fichier via `git show HEAD:"path/to/file" > path/to/file`.  
**Erreur** : Si le fichier n'existe pas dans HEAD (jamais commité), git écrit le message d'erreur (`fatal: path '...' exists on disk, but not in 'HEAD'`) dans la destination via la redirection PowerShell.  
**Règle** : Avant tout `git show HEAD:...`, vérifier : `git ls-files --error-unmatch path/to/file`. Pour un fichier non commité corrompu, utiliser `replace_string_in_file` directement.

---

### L-11 · Stop-loss exécuté côté exchange sans alerte email ni mise à jour d'état
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-03-19

**Contexte** : Un ordre `STOP_LOSS_LIMIT` déclenché par l'exchange en production clôture la position.  
**Erreur** : Le bot ne détectait pas les clôtures initiées côté exchange (`FILLED` sur `sl_order_id`). Aucune notification email n'était envoyée, `pair_state` restait en `in_position=True` → le bot continuait à gérer une position qui n'existait plus.  
**Règle** : La réconciliation API (`position_reconciler`) doit être appelée à chaque cycle pour détecter les ordres filled côté exchange. Une clôture détectée via `sl_order_id` doit : (1) remettre `in_position=False` + `sl_order_id=None` + `sl_exchange_placed=False`, (2) envoyer une notification email SELL avec le motif `"stop_loss_filled"`.  
**Ref** : Audit email alerts 2026-03-19 — EM-P1-01 / `position_reconciler.py`

---

## Backtest

### L-13 · `backtest_taker_fee` écrasé par les fees live au runtime
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-03-19

**Contexte** : Agent modifie `config.backtest_taker_fee` ou `config.backtest_maker_fee` pour "aligner" les fees backtest avec les fees live.  
**Erreur** : Les fees backtest sont **figés par design** pour garantir la reproductibilité des résultats. Les écraser au runtime invalide toutes les comparaisons IS/OOS historiques et fausse les métriques de walk-forward.  
**Règle** : `backtest_taker_fee` et `backtest_maker_fee` sont des constantes de configuration — immuables au runtime. Uniquement modifiables dans `backtest_runner.py` de façon explicite. Ne jamais les "synchroniser" avec `taker_fee` / `maker_fee` live.  
**Ref** : `.github/copilot-instructions.md` section Fees

---

## Tests

### L-12 · `_execute_buy()` — `display_buy_signal_panel` doit être patché dans les tests de la chaîne SL
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-03-19

**Contexte** : Test E2E de la chaîne P0-STOP (`SL-fail → rollback → emergency_halt`) via `_execute_buy()`.  
**Erreur** : `display_buy_signal_panel()` est appelée *après* la logique SL et accède à `row['ema1']`, `row['ema2']`, `row['stoch_rsi']` — absentes du mock minimal. Le test crashe avec `KeyError` alors que la logique testée a bien fonctionné (visible dans les logs capturés).  
**Règle** : Lors du test de `_execute_buy()`, toujours patcher `order_manager.display_buy_signal_panel`. C'est une UI side-effect sans lien avec la logique métier testée.  
**Pattern correct** :
```python
with patch('order_manager.display_buy_signal_panel'):
    _execute_buy(ctx, deps)
```
**Ref** : `tests/test_order_manager_sl_chain.py` (TS-P2-03, 2026-03-19)

---

## Cython

### L-14 · Stub `.pyi` orphelin — fonction déclarée absente du `.pyx`
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-03-20

**Contexte** : Audit Cython de `backtest_engine_standard.pyi` — `calculate_indicators_fast()` déclarée dans le stub, absente de `backtest_engine_standard.pyx`.  
**Erreur** : Pyright / mypy ne signalent aucune erreur (le stub fait foi), mais l'appel échoue avec `AttributeError` à l'exécution car la fonction n'existe pas dans le `.pyd` compilé.  
**Règle** : Après toute restructuration Cython (ajout, renommage ou suppression de fonction dans un `.pyx`), vérifier la cohérence `.pyx` ↔ `.pyi` avec :
```powershell
grep -n "^def " code/<module>.pyx          # fonctions publiques dans la source
grep -n "^def " code/bin/<module>.pyi      # fonctions déclarées dans le stub
```
Toute fonction dans `.pyi` sans `def` correspondant dans `.pyx` est un stub orphelin à supprimer.  
**Ref** : audit_cython_multi_assets.md — BLOC 2 · C-01 · commit post-60a0a0f

---

### L-15 · Module Cython compilé mais jamais importé en production
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-03-20

**Contexte** : `backtest_engine` (legacy) compilé pour cp311 + cp313, listé dans `config/setup.py`, mais aucun import dans `code/src/` — uniquement `backtest_engine_standard` est utilisé en production.  
**Erreur** : Le module legacy dérive silencieusement à chaque évolution du moteur actif. Sa signature incompatible (`open_prices` pos 2 vs 11, DEF constants vs runtime params) peut induire des bugs difficiles à diagnostiquer si quelqu'un l'importe par erreur ou confusion de nom.  
**Règle** : Avant tout audit Cython, vérifier pour chaque `.pyx` / `.pyd` qu'il est effectivement importé en production :
```powershell
grep -r "import <module_name>" code/src/
```
Si aucun résultat → archiver dans `code/legacy/` et retirer de `config/setup.py`.  
**Action appliquée** : `backtest_engine.pyx` + `.cpp` → `code/legacy/` ; `.pyd` + `.pyi` → `code/bin/legacy/` ; extension retirée de `config/setup.py`.  
**Ref** : audit_cython_multi_assets.md — BLOC 3 · C-02 · commit post-60a0a0f

---

## Git & Hygiène repo

### L-16 · Fichiers runtime non ignorés par git
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-04-16

**Contexte** : Des fichiers générés au runtime (`.running.lock`, `*.tmp`, `*.lock`) apparaissent dans `git status` comme modifiés ou non-suivis.  
**Erreur** : Ces fichiers ne doivent jamais être commités — ils polluent l'historique git et sont spécifiques à la machine.  
**Règle** : **RÈGLE ABSOLUE** — Après toute modification impliquant un fichier runtime (lock, tmp, pid, artefact outil), vérifier `git status` et ajouter immédiatement la règle dans `.gitignore` **sans attendre que l'utilisateur le demande**. Commiter `.gitignore` dans la même session.  
**Patterns à ignorer systématiquement** :
- `*.lock` — fichiers de verrou runtime (PID bot)
- `*.tmp` — artefacts outils (pyright, etc.)
- `*.pid` — fichiers de processus
- Tout fichier dont le contenu est un PID ou un timestamp machine  
**Ref** : commit `5165abc` · commit `27ca8d0`

---

### L-17 · Sync API écrase les flags partiels locaux True→False → boucle infinie de ventes
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-04-17

**Contexte** : `check_partial_exits_from_history` vérifie les ventes partielles via `get_my_trades`. La sync dans `_execute_partial_sells` traitait l'API comme "source de vérité" et écrasait les flags locaux.  
**Erreur** : Binance découpe les market orders en multiples fills (order book). La détection comptait chaque fill individuellement (ratio ~25% au lieu de ~50%) → retournait False. La sync écrasait `partial_taken_1=True` (correct) → `False` (incorrect) → le bot ré-exécutait PARTIAL-1 toutes les 2 minutes, vendant la moitié de la position restante à chaque cycle. **Perte de capital réelle.**  
**Règles** :
1. **JAMAIS** downgrader un flag partial de True→False via une heuristique API. Les flags locaux posés par `_execute_one_partial` sont la source de vérité.
2. Grouper les fills par `orderId` avant de calculer le ratio quantité/prix pour la détection de partiels.
3. Tester les fonctions de détection avec des données réelles multi-fill, pas seulement des ordres mono-fill.  
**Ref** : commits `a52d8e5` + `067d996` · `order_manager.py` sync logic · `trade_helpers.py` fills grouping

---

### L-18 · Reconciler réinitialise inconditionnellement les flags de position
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-04-17

**Contexte** : `_handle_pair_discrepancy` dans `position_reconciler.py` restaure une position orpheline.  
**Erreur** : Le code faisait `partial_taken_1=False, partial_taken_2=False` inconditionnellement, même si le state contenait les bonnes valeurs. Combiné avec L-17 (détection API défaillante), les flags étaient perdus à chaque redémarrage.  
**Règle** : Lors de la restauration d'un état, **toujours préserver les flags existants** plutôt que les réinitialiser. Pattern : `_p1 = existing.get('partial_taken_1', False)` avant l'update.  
**Ref** : commit `a52d8e5` · `position_reconciler.py`

---

### L-19 · SL exchange annulé mais jamais restauré si la vente partielle échoue
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-04-17

**Contexte** : `_execute_partial_sells` annule le SL exchange (via `_cancel_exchange_sl`) AVANT d'appeler `_execute_one_partial`. Si la vente est bloquée (MIN_NOTIONAL, LOT_SIZE), échoue (NOT FILLED), ou lève une exception, le SL n'est jamais re-placé.  
**Erreur** : En production, PARTIAL-2 déclenchée pour ONDOUSDC → SL annulé → notional 4.18 USDC < MIN_NOTIONAL 10.00 → vente bloquée → position sans SL pendant plusieurs cycles (alerte `[SL-MANQUANT]` 2 min plus tard). Le détecteur `[SL-MANQUANT]` dans l'orchestrateur envoie un email mais ne prend aucune action corrective.  
**Règles** :
1. Tout chemin d'échec dans `_execute_one_partial` DOIT appeler `_restore_exchange_sl` pour remettre le SL.
2. Ne jamais annuler un SL sans garantir qu'il sera re-placé dans TOUS les chemins (succès, échec, exception).
3. C'est le 3ème variant du pattern "SL cancelled but not replaced" (après P0-01 et L-11).  
**Ref** : `order_manager.py` `_restore_exchange_sl()` — commit fixant les 3 chemins (min_notional, not filled, exception)

---

### L-20 · `sl_exchange_placed=True` perdu après save → état incohérent
**Sévérité** : 🟠 HAUTE · **Date** : 2026-04-17

**Contexte** : Le `position_reconciler` (C-11) pose un SL, set `sl_exchange_placed=True`, et appelle `save_bot_state(force=True)`. 5 secondes plus tard, `_execute_real_trades_inner` lit `sl_exchange_placed=False` malgré `sl_order_id` présent.
**Cause probable** : Race condition ou format legacy dans `bot_state.json` (clé `"bot_state"` imbriquée créant un dict fantôme).
**Fixes** :
1. Migration automatique du format legacy `{"bot_state": {...}}` dans `load_state()`.
2. Auto-correction dans le check `[SL-MANQUANT]` : si `sl_order_id` présent, vérifier sur Binance si le SL est actif. Si oui, corriger `sl_exchange_placed=True` et sauvegarder.
3. Diagnostic logging dans le reconciler post-save pour tracer le problème si récidive.
**Ref** : `state_manager.py` migration F-SL-FIX, `MULTI_SYMBOLS.py` auto-correction F-SL-FIX

---

## Observabilité & Launchers

### L-21 · Launcher basé sur `Start-Sleep` au lieu d'un signal de readiness réel
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-04-22

**Contexte** : Un script de lancement démarre le bot ou le dashboard puis attend un délai fixe avant d'ouvrir la console ou le navigateur.
**Erreur** : Le service peut être encore indisponible, ou au contraire déjà prêt bien avant. Résultat : UI ouverte sur une API KO, faux diagnostics, et comportements non déterministes selon la machine.
**Règle** : Ne jamais utiliser un sleep fixe comme critère de readiness. Toujours attendre un signal métier réel :
1. Dashboard : endpoint HTTP `/api/data` en 2xx.
2. Bot : heartbeat frais + PID vivant + `loop_counter >= 1`.
**Ref** : `code/scripts/wait_for_dashboard_ready.py`, `code/scripts/wait_for_bot_ready.py`, `code/scripts/manage_task.bat`

---

### L-22 · PID du heartbeat plus fiable que `.running.lock` quand plusieurs processus coexistent
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-04-22

**Contexte** : `start_safe.ps1` ou un helper de readiness compare un PID de lock avec un PID actif vu ailleurs (heartbeat, processus Python multiples, relance console/hidden).
**Erreur** : Le lock peut être stale ou refléter un ancien processus alors que le heartbeat identifie correctement l'instance réellement vivante. Suivre aveuglément le lock produit des faux positifs, des refus de démarrage, ou une attente infinie.
**Règle** : En cas de divergence, traiter le heartbeat comme source de vérité seulement si son PID est vivant et que le PID du lock ne l'est plus. Si le lock PID est encore vivant mais différent, considérer la situation comme incohérente et ne pas la masquer.
**Ref** : `start_safe.ps1`, `code/scripts/wait_for_bot_ready.py`

---

## Référence — Patterns P0 appliqués (historique)

> Ces patterns sont actifs dans le code. Mettre à jour si une correction change le comportement.

| P0 | Symptôme | Pattern appliqué |
|----|----------|-----------------|
| P0-01 (SL non garanti) | Position ouverte sans stop-loss en cas d'échec API | `OrderError` + `safe_market_sell` d'urgence + `sl_exchange_placed` persisté |
| P0-02 (balance 0 silencieux) | Achat tenté avec balance USDC = 0 | `BalanceUnavailableError` → cycle sauté, pas de buy |
| P0-03 (OOS gate bypassé) | `oos_blocked` remis à False au redémarrage | `oos_blocked` persisté dans `bot_state`, pas purgé par `load_bot_state` |
| P0-04 (HMAC hardcodé) | Clé HMAC figée dans le code source | Clé HMAC = `BINANCE_SECRET_KEY`, `EnvironmentError` si absente |
| P0-05 (SizingError silencieuse) | Erreur de sizing ignorée → ordre mal dimensionné | `SizingError` levée proprement, catchée dans `_execute_buy()` |
| P0-SAVE (save silencieux) | Échecs `save_bot_state()` non détectés | 3 failures consécutives → `emergency_halt = True` + alerte CRITICAL email |

---

## Transition DEMO → LIVE

### L-17 · DRY-RUN residue dans `pair_state` après passage LIVE
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-04-17

**Contexte** : Passage de BOT_MODE=DEMO à BOT_MODE=LIVE sans purge de l'état.
**Erreur** : `sl_order_id = "DRYRUN-SL-0"` (ID non-numérique) persiste dans `pair_state`. Le `position_reconciler` le passe à `client.get_order()` → `BinanceAPIException` (illegal characters).
**Règle** : Avant tout appel API avec un `sl_order_id`, vérifier `str(sl_order_id).isdigit()`. Les IDs DRY-RUN (`DRYRUN-*`) ne sont pas des IDs Binance valides.
**Pattern correct** :
```python
if _sl_oid and not str(_sl_oid).isdigit():
    logger.warning("[RECONCILE] SL ID non-numérique ignoré: %s", _sl_oid)
    _sl_oid = None
```
**Ref** : D-03 — `position_reconciler.py` L184-190

---

### L-18 · Valeur déjà en pourcentage multipliée à nouveau par 100
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-04-17

**Contexte** : Log Optuna OOS affichait `WR=6666.7%` au lieu de `66.67%`.
**Erreur** : `avg_oos_win_rate` retourné par le backtest engine est déjà en `%` (ex: 66.67). Le code log faisait `avg_oos_win_rate * 100.0` → 6666.7%.
**Règle** : Toujours vérifier l'unité d'une métrique avant de la formater. Le backtest engine retourne `win_rate` en pourcentage (0–100), pas en fraction (0–1).
**Ref** : D-02 — `walk_forward.py` L872

---

### L-19 · `initial_position_size` = quantité pré-exécution au lieu de nette
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-04-17

**Contexte** : Dashboard affichait QTY=814.4 au lieu de 809.2 (nette après commission).
**Erreur** : `pair_state['initial_position_size']` était set à `quantity_rounded` (quantité calculée avant exécution) au lieu de `actual_qty_str` (quantité nette après commission Binance).
**Règle** : Pour toute quantité persistée dans `pair_state`, utiliser la quantité POST-exécution (`executedQty - commission`), jamais la quantité calculée pré-order.
**Ref** : D-05 — `order_manager.py` L1524

---

### L-20 · `starting_equity` absent du tracker → dashboard fallback incorrect
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-04-17

**Contexte** : Dashboard affichait Daily Loss Limit = 1.86 USDC (5% × 37.13 USDC libre) au lieu de 500 USDC (5% × 10000).
**Erreur** : `_daily_pnl_tracker` ne contenait pas `starting_equity`. Le JS dashboard fallait sur `d.usdc_balance` (solde libre, pas l'equity totale).
**Règle** : Toute donnée affichée par le dashboard doit être explicitement persistée dans `bot_state`. Ne pas compter sur des fallbacks JS côté client.
**Ref** : D-06 — `MULTI_SYMBOLS.py` après L1411

---

### L-21 · BUY en boucle — 3 failles combinées (save non-forcé + pas de guard state + dust reset)
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-04-17

**Contexte** : En production, 19 ordres BUY ONDOUSDC exécutés sur Binance en 36 minutes (06:22→06:58 UTC). Trade journal montre 18 entrées consécutives identiques (qty=799.5, equity_before=252.18).  
**Erreur** : 3 failles combinées dans `order_manager.py` :
1. `deps.save_fn()` sans `force=True` après BUY → état throttlé 5s → si PM2 restart entre cycles, `last_order_side` non persisté → pair re-initialisée sans historique → `last_order_side=None` → buy autorisé à nouveau.
2. `_validate_buy_preconditions()` ne vérifiait PAS `last_order_side == 'BUY'` → pas de guard état interne.
3. `_handle_dust_cleanup()` resetait `last_order_side='SELL'` si dust notional < min_notional ET `last_order_side=='BUY'` → boucle d'achat si position réelle trop petite pour être vendue.

**Règle** :
- Après chaque BUY confirmé FILLED, toujours `save_fn(force=True)` — l'état post-achat est critique (capital engagé).
- `_validate_buy_preconditions()` doit être le premier guard explicite sur `last_order_side=='BUY'`.
- Tout reset de `last_order_side BUY→SELL` dans dust cleanup doit logger `CRITICAL` (capital risqué si position réelle).

**Pattern correct** :
```python
# Dans _validate_buy_preconditions — premier guard
if ps.get('last_order_side') == 'BUY':
    logger.info("[BUY BLOCKED P0-BUY] %s — last_order_side='BUY'", ctx.backtest_pair)
    return False

# Après BUY confirmé
ps.update({..., 'last_order_side': 'BUY', ...})
deps.save_fn(force=True)  # OBLIGATOIRE — jamais save_fn() après un achat
```
**Ref** : commit `5b48c42` · `order_manager.py` L1270, L1502, L923

---

### L-22 · SL exchange FILLED mais balance=0 → P0-BUY bloque indéfiniment
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-04-26

**Contexte** : ONDOUSDC — SL STOP_LOSS_LIMIT rempli par Binance, balance tombe à exactement 0. Le bot répète `[BUY BLOCKED P0-BUY]` toutes les 2 min pendant 2+ heures. Aucun buy possible.
**Erreur** : 3 chemins runtime échouent tous quand `coin_balance == 0` :
1. `_check_and_execute_stop_loss` bails à `ctx.coin_balance <= 0` (L910) → ne détecte jamais le SL fill
2. `_handle_dust_cleanup` : `coin_balance > min_qty * 0.01` faux quand balance=0 → pas de dust détecté
3. `_sync_order_history` : partial-sell guard bloque la mise à jour `BUY→SELL` car `entry_price is not None`
Seul le reconciler (startup) corrigeait l'état → si le bot ne redémarre pas, blocage permanent.
**Fix** : Nouvelle fonction `_reconcile_zero_balance_sl()` appelée dans `_check_and_execute_stop_loss` quand `last_order_side='BUY'` et `coin_balance <= 0`. Vérifie le SL sur Binance, reset complet de l'état, email d'alerte.
**Règle** : Tout guard `coin_balance > 0` dans le chemin SL doit avoir un fallback pour balance=0 avec last_order_side='BUY'.
**Ref** : `order_manager.py` · `_reconcile_zero_balance_sl()`

---

### L-23 · Cooldown A-3 calculé depuis time.time() au lieu du fill Binance → blocage persistant
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-04-27

**Contexte** : Après un SL fill, le reconciler et `_reconcile_zero_balance_sl` calculaient le cooldown A-3 avec `time.time()` (heure de réconciliation) au lieu du `updateTime` Binance (heure réelle du fill). Si le bot redémarre 27h après le fill, le cooldown (12h) est recalculé depuis maintenant → bloque 12h de plus alors que le vrai cooldown a expiré depuis 15h.
**Erreur 2** : `entry_timeframe` lu APRÈS `update_pair_state(reset)` → toujours `None` → fallback 1h au lieu du vrai 4h.
**Erreur 3** : La valeur stale `_stop_loss_cooldown_until` persiste dans bot_state.json. Le fix du calcul ne corrige que les NOUVEAUX cooldowns, pas la valeur déjà écrite.
**Fix** :
1. Cooldown utilise `updateTime` de l'ordre Binance (ms → s) comme base
2. `_saved_entry_tf` extrait AVANT le reset de pair_state
3. Branche "cohérent" du reconciler : startup re-validation — si cooldown > now mais le fill Binance est assez ancien → recalcule ou supprime
**Règle** : Tout cooldown basé sur un event exchange doit utiliser le timestamp exchange, jamais `time.time()`. Les valeurs persistées doivent être re-validées au startup.
**Ref** : `position_reconciler.py` · `order_manager.py`

---

### L-25 · Stop-loss exchange "périmé" après breakeven/trailing — ordre zombie Binance
**Sévérité** : 🔴 CRITIQUE · **Date** : 2026-04-27

**Contexte** : Après un BUY, un ordre `STOP_LOSS_LIMIT` est posé sur Binance au niveau ATR (`stop_loss_at_entry`). Si le breakeven ou le trailing relève `effective_stop` au-dessus de l'ATR, l'ordre Binance n'est **pas remplacé** — il reste sur l'exchange avec l'ancien `stopPrice`. Le bot voit `sl_exchange_placed=True + statut=NEW` et diffère indéfiniment à Binance. Binance ne fill jamais car le prix n'atteint pas l'ancien niveau ATR (le nouveau stop est plus haut).
**Symptôme** : ONDO breakeven à 0.2675, ordre ATR à 0.2518 — prix redescend à 0.261 → pas de vente.
**Fix** : Bloc `[SL-SYNC]` dans `_check_and_execute_stop_loss` : si `effective_stop > stop_loss_at_entry + ε` et `sl_exchange_placed=True`, cancel l'ordre Binance + `_handle_manual_sl_trigger` immédiat.
**Fix 2** : Bloc `[SL-SYNC-GUARD]` dans `_handle_manual_sl_trigger` : cancel défensif si `sl_order_id` présent avec coins libres (état inconsistant post-réconciliation).
**Règle** : Après tout relèvement de stop (breakeven, trailing), l'ordre exchange est obsolète. Le bot NE remplace PAS proactivement l'ordre — il détecte + cancel au moment du trigger. Ne jamais supposer que Binance va fill si `effective_stop ≠ stop_loss_at_entry`.
**Ref** : `order_manager.py` · `_check_and_execute_stop_loss` (L-1101) + `_handle_manual_sl_trigger` (L-743)

### L-24 · Export metrics — `pairs` peut contenir des dicts non hashables
**Sévérité** : 🟡 IMPORTANT · **Date** : 2026-04-22

**Contexte** : Le scheduler appelle `write_metrics(...)` avec `pairs=[p['backtest_pair'] for p in crypto_pairs]`. Selon la forme de `crypto_pairs`, certaines entrées peuvent être des dicts complets (ou hybrides) au lieu de chaînes.
**Erreur** : `bot_state.get(pair)` reçoit un dict comme clé → `TypeError: unhashable type: 'dict'` et warning récurrent `[METRICS] Échec écriture metrics.json`.
**Règle** : Toute API utilitaire qui accepte une collection de paires doit normaliser les entrées en `str` et ignorer les éléments non résolvables. Supporter explicitement les clés `backtest_pair`, `real_pair`, `pair`, `symbol`.
**Ref** : `metrics.py` · `_normalize_pairs()` + test `test_write_accepts_pairs_as_dict_entries`
