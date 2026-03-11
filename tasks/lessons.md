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

## L-08 · Supposer une version Python sans vérifier le venv

**Contexte** : Décision de "version Python unifiée" prise sans vérifier `.venv/Scripts/python.exe --version`.
**Erreur** : Version 3.13 propagée partout alors que le venv local tourne en **3.11.9**. Le `.cp311.pyd` est le binaire actif — modifier le CI vers 3.13 aurait cassé la production.
**Règle** : Toujours exécuter `.venv\Scripts\python.exe --version` avant toute décision de version. Les `.pyd` dans `code/bin/` indiquent les versions actives (`.cp311` = Python 3.11, `.cp313` = Python 3.13).
**Action corrective** : Revert README, requirements.txt, main.yml, pyproject.toml, copilot-instructions.md. Recompiler les `.pyd` manquants avec `config/setup.py build_ext --inplace --force`, copier dans `code/bin/`.

---

## L-07 · `python.analysis.extraPaths` en conflit avec `pyrightconfig.json`

**Contexte** : `python.analysis.extraPaths` défini dans `.vscode/settings.json` alors que `pyrightconfig.json` existe à la racine.
**Erreur** : Pylance affiche un warning et ignore le paramètre — les deux configurations se contredisent, résolution des imports non garantie.
**Règle** : Quand `pyrightconfig.json` est présent, toute la config des chemins (`extraPaths`, `stubPath`) doit être dans `pyrightconfig.json` uniquement. Supprimer `python.analysis.extraPaths` de `settings.json`.
**Ref** : commit `4b23841`
