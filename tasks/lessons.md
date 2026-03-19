# MULTI_ASSETS — Leçons apprises (Self-Improvement Loop)

> Lire ce fichier au début de chaque session.
> Mettre à jour après toute correction de l'utilisateur.
> Chaque entrée = un pattern d'erreur à ne plus reproduire.

---

## L-01 · `TRAILING_STOP_MARKET` généré sur Binance Spot

**Contexte** : Agent IA génère du code utilisant `TRAILING_STOP_MARKET` (ex: ordre de stop dynamique).
**Erreur** : Ce type d'ordre n'existe pas sur Binance Spot → lève `NotImplementedError` au runtime.
**Règle** : Stop-loss = `STOP_LOSS_LIMIT` uniquement, posé immédiatement après chaque BUY.
**Ref** : `.claude/rules.md` interdiction #5

---

## L-02 · `start_date` figée à l'import

**Contexte** : Agent initialise `start_date = datetime.now() - timedelta(days=1095)` en dehors d'une fonction.
**Erreur** : La date se fige au moment de l'import du module, créant un biais temporel croissant au fil du temps.
**Règle** : Toujours utiliser `_fresh_start_date()` — calculé dynamiquement à chaque appel.
**Ref** : `.claude/rules.md` interdiction #4

---

## L-03 · Écriture dans `bot_state` sans `_bot_state_lock`

**Contexte** : Agent ajoute ou modifie une clé dans `bot_state[pair]` directement, hors contexte de verrou.
**Erreur** : Race condition possible avec le thread scheduler (toutes les 2 minutes par paire).
**Règle** : Toute écriture dans `bot_state` doit être encapsulée dans `with _bot_state_lock:`.
**Ref** : `.claude/rules.md` interdiction #6

---

## L-04 · Pyright `typeCheckingMode: "basic"` → 91 faux positifs Pandas en CI

**Contexte** : Tentative d'activation de `typeCheckingMode: "basic"` dans `pyrightconfig.json` pour renforcer le type checking.
**Erreur** : Les stubs Pandas génèrent 91 erreurs (reportArgumentType, reportAttributeAccessIssue, reportReturnType, etc.) — tous faux positifs sur des subscripts `df["col"]`.
**Règle** : Garder `typeCheckingMode: "off"` avec `reportMissingImports: "error"` et `reportUndefinedVariable: "error"` explicites. Ne pas reverter vers "basic".
**Ref** : `pyrightconfig.json` — commit `4b23841`

---

## L-05 · Versions Python contradictoires entre fichiers

**Contexte** : Modification d'un seul fichier mentionnant la version Python (ex: README, CI, requirements).
**Erreur** : Divergence entre les sources (copilot-instructions, main.yml, requirements.txt, README, pyproject.toml).
**Règle** : Source de vérité = `pyproject.toml` (`requires-python = ">=3.11"`). Version réelle du venv local : **Python 3.11.9**. Mettre à jour tous les fichiers simultanément lors d'un changement de version. Ne jamais supposer une version sans vérifier `.venv/Scripts/python.exe --version`.
**Ref** : `docs/STRUCTURAL_AUDIT_ACTION_PLAN.md` — P0-2

---

## L-06 · `pytest.ini` a priorité sur `pyproject.toml`

**Contexte** : Les deux fichiers coexistent avec des sections de configuration pytest.
**Erreur** : `pytest.ini` prend la priorité selon la hiérarchie pytest, rendant `[tool.pytest.ini_options]` dans `pyproject.toml` silencieusement ignoré.
**Règle** : Un seul fichier de config pytest — `pyproject.toml`. `pytest.ini` a été supprimé (commit `8cc5c0a`). Ne jamais recréer `pytest.ini`.
**Ref** : `docs/STRUCTURAL_AUDIT_ACTION_PLAN.md` — P1-3

---

## L-07 · `python.analysis.extraPaths` en conflit avec `pyrightconfig.json`

**Contexte** : `python.analysis.extraPaths` défini dans `.vscode/settings.json` alors que `pyrightconfig.json` existe à la racine.
**Erreur** : Pylance affiche un warning et ignore le paramètre — les deux configurations se contredisent, résolution des imports non garantie.
**Règle** : Quand `pyrightconfig.json` est présent, toute la config des chemins (`extraPaths`, `stubPath`) doit être dans `pyrightconfig.json` uniquement. Supprimer `python.analysis.extraPaths` de `settings.json`.
**Ref** : commit `4b23841`

---

## L-08 · Supposer une version Python sans vérifier le venv

**Contexte** : Décision de "version Python unifiée" prise sans vérifier `.venv/Scripts/python.exe --version`.
**Erreur** : Version 3.13 propagée partout alors que le venv local tourne en **3.11.9**. Le `.cp311.pyd` est le binaire actif — modifier le CI vers 3.13 aurait cassé la production.
**Règle** : Toujours exécuter `.venv\Scripts\python.exe --version` avant toute décision de version. Les `.pyd` dans `code/bin/` indiquent les versions actives (`.cp311` = Python 3.11, `.cp313` = Python 3.13).
**Action corrective** : Revert README, requirements.txt, main.yml, pyproject.toml, copilot-instructions.md. Recompiler les `.pyd` manquants avec `config/setup.py build_ext --inplace --force`, copier dans `code/bin/`.

---

## L-09 · PowerShell `Set-Content -Encoding UTF8` écrit un BOM UTF-8

**Contexte** : Tentative de modifier un fichier `.py` via PowerShell `Set-Content ... -Encoding UTF8`.
**Erreur** : PowerShell 5.1 écrit systématiquement un BOM (`\xEF\xBB\xBF`) en tête du fichier. Python interprète le BOM comme faisant partie du premier token, corrompant la syntaxe (ex: `# pylint` devient ` pylint`).
**Règle** : Ne **jamais** utiliser `Set-Content` pour modifier des fichiers `.py` ou `.md`. Utiliser exclusivement `replace_string_in_file` ou Python `open(..., 'w', encoding='utf-8')`. Si PowerShell est obligatoire : `[System.IO.File]::WriteAllText(path, content, [System.Text.UTF8Encoding]::new($false))`.

---

## L-10 · `git show HEAD:path > fichier` écrase avec l'erreur git si le fichier n'est pas en HEAD

**Contexte** : Tentative de restaurer un fichier corrompu via `git show HEAD:"path/to/file" > path/to/file`.
**Erreur** : Si le fichier n'existe pas dans HEAD (jamais commité), git écrit le message d'erreur (`fatal: path '...' exists on disk, but not in 'HEAD'`) dans le fichier de destination via la redirection PowerShell.
**Règle** : Avant tout `git show HEAD:...`, vérifier que le fichier est bien tracké : `git ls-files --error-unmatch path/to/file`. Pour restaurer un fichier non-commité corrompu, utiliser `replace_string_in_file` pour réécrire le contenu directement.

---

## L-11 · Patterns P0 appliqués dans le projet (référence historique)

| P0 | Symptôme | Pattern appliqué |
|----|----------|-----------------|
| P0-01 (SL non garanti) | Position ouverte sans stop-loss en cas d'échec API | `OrderError` + `safe_market_sell` d'urgence + `sl_exchange_placed` persisté |
| P0-02 (balance 0 silencieux) | Achat tenté avec balance USDC = 0 | `BalanceUnavailableError` → cycle sauté, pas de buy |
| P0-03 (OOS gate bypassé) | `oos_blocked` remis à False au redémarrage | `oos_blocked` persisté dans `bot_state`, pas purgé par `load_bot_state` |
| P0-04 (HMAC hardcodé) | Clé HMAC figée dans le code source | Clé HMAC = `BINANCE_SECRET_KEY`, `EnvironmentError` si absente |
| P0-05 (SizingError silencieuse) | Erreur de sizing ignorée → ordre mal dimensionné | `SizingError` levée proprement, catchée dans `_execute_buy()` |
| P0-SAVE (save silencieux) | Échecs `save_bot_state()` non détectés | 3 failures consécutives → `emergency_halt = True` + alerte CRITICAL email |

---

## L-12 · `_execute_buy()` — `display_buy_signal_panel` doit être patché dans les tests de la chaîne SL

**Contexte** : Test E2E de la chaîne P0-STOP (`SL-fail → rollback → emergency_halt`) via `_execute_buy()`.
**Erreur** : `display_buy_signal_panel()` est appelée *après* la logique SL et accède à `row['ema1']`, `row['ema2']`, `row['stoch_rsi']`, etc. — des clés absentes du mock minimal. Le test crashe avec `KeyError` après que la logique testée a pourtant bien fonctionné (visible dans les logs capturés).
**Règle** : Lors du test de `_execute_buy()`, toujours patcher `order_manager.display_buy_signal_panel` avec `unittest.mock.patch`. Cette fonction est une UI side-effect sans lien avec la logique métier testée — inutile de la remplir en données.
**Pattern correct** :
```python
with patch('order_manager.display_buy_signal_panel'):
    _execute_buy(ctx, deps)
```
**Ref** : `tests/test_order_manager_sl_chain.py` (TS-P2-03, 2026-03-19)
