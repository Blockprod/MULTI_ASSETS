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
