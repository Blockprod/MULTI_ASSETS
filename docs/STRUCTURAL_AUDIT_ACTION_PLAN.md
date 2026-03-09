# Plan d'Action — Audit Structurel MULTI_ASSETS

> Généré le 09/03/2026 — Basé sur l'audit structurel en 6 dimensions (A→F).  
> **Principe** : priorités P0 → P1 → P2 dans l'ordre strict. Chaque item est autonome.  
> Valider `pytest tests/ -x -q` après chaque sprint.

---

## Table des matières

- [SPRINT 0 — P0 Bloquants immédiats](#sprint-0--p0-bloquants-immédiats)
- [SPRINT 1 — P1 Haute priorité](#sprint-1--p1-haute-priorité)
- [SPRINT 2 — P2 Améliorations](#sprint-2--p2-améliorations)
- [Validation finale](#validation-finale)

---

## SPRINT 0 — P0 Bloquants immédiats

> Ces trois items créent des **hallucinations actives** pour tout agent IA et exposent
> des données sensibles. À corriger avant tout autre travail.

---

### P0-1 · Corriger la contradiction "Trailing Stop" dans le README

**Fichier :** `README.md`  
**Risque :** Tout agent IA lisant le README génère du code `TRAILING_STOP_MARKET` — interdit sur Spot Binance.

**Action :**

Dans la section `## Fonctionnalités`, remplacer :
```
- **Trailing stop** : Activation dynamique basée sur l'ATR
```
par :
```
- **Stop-loss natif** : `STOP_LOSS_LIMIT` exchange-natif posé immédiatement après chaque BUY (le `TRAILING_STOP_MARKET` n'existe pas sur Binance Spot)
```

- [x] Modifier `README.md` section Fonctionnalités
- [x] Vérifier qu'aucune autre mention de "trailing stop" dans le README ne sous-entend `TRAILING_STOP_MARKET`

---

### P0-2 · Unifier la version Python sur une seule source de vérité

**Fichier concernés :** `.github/copilot-instructions.md`, `.github/workflows/main.yml`,
`requirements.txt`, `README.md`, `pyproject.toml`  
**Risque :** 4 sources contradictoires (3.13, 3.11, 3.11.9, 3.11+). L'agent IA et la CI divergent.

**Source de vérité choisie : Python 3.13** (conforme à `.venv` local et copilot-instructions actuel).

**Actions :**

1. **`README.md`** — section Prérequis :
   ```
   # Avant
   - **Python 3.11+**
   # Après
   - **Python 3.13+**
   ```

2. **`requirements.txt`** — en-tête :
   ```
   # Avant
   # MULTI_ASSETS TRADING BOT - DEPENDENCIES (Python 3.11.9)
   # Après
   # MULTI_ASSETS TRADING BOT - DEPENDENCIES (Python 3.13)
   ```

3. **`.github/workflows/main.yml`** — section `Set up Python` :
   ```yaml
   # Avant
   python-version: "3.11"
   # Après
   python-version: "3.13"
   ```

4. **`pyproject.toml`** — ajouter sous `[tool.ruff]` :
   ```toml
   [project]
   requires-python = ">=3.13"
   ```

- [x] Modifier `README.md`
- [x] Modifier `requirements.txt`
- [x] Modifier `.github/workflows/main.yml`
- [x] Ajouter `requires-python` dans `pyproject.toml`
- [x] Vérifier : `python --version` dans `.venv` → doit être 3.13.x

---

### P0-3 · Exclure `*.csv` et `*.meta.json` du suivi git

**Fichier :** `.gitignore`  
**Risque :** `config/trades_export.csv` et tout fichier `*.meta.json` contiennent des données de trading réelles (prix, quantités, PnL). Leur commit dans git expose des données financières privées.

**Action — ajouter dans `.gitignore`** après la section `# Logs` :
```gitignore
# Données de trading (runtime artifacts — ne jamais committer)
*.csv
*.meta.json
config/trades_export.csv
```

**Vérifier qu'aucun CSV n'est déjà tracké :**
```powershell
git ls-files "*.csv" "*.meta.json"
```
Si des fichiers remontent, les désindexer :
```powershell
git rm --cached config/trades_export.csv
git rm --cached "*.meta.json"
```

- [x] Ajouter les patterns dans `.gitignore`
- [x] Exécuter `git ls-files "*.csv" "*.meta.json"` et nettoyer si nécessaire
- [x] Committer `.gitignore` uniquement : `git add .gitignore ; git commit -m "chore: exclude runtime CSV and meta.json from tracking"`

---

## SPRINT 1 — P1 Haute priorité

---

### P1-1 · Retirer `.vscode/` du `.gitignore` et committer `settings.json`

**Fichier :** `.gitignore`, `.vscode/settings.json`  
**Risque :** Sans `settings.json` commité, tout clone perd : interpréteur Python, résolution
des imports `code/src` et `code/bin`, associations Cython (`.pyx`/`.pxd`), `diagnosticMode: workspace`.
Pylance signale alors des centaines de faux positifs sur les stubs Cython.

**Actions :**

1. **Retirer la ligne `.vscode/` du `.gitignore`** et la remplacer par une exclusion sélective :
   ```gitignore
   # Avant
   .vscode/
   
   # Après
   .vscode/*.code-workspace
   ```
   > Cela committera `settings.json` sans jamais committer les fichiers workspace locaux.

2. **Corriger le conflit de ruler dans `.vscode/settings.json`** :
   ```json
   // Avant
   "editor.rulers": [120],
   // Après
   "editor.rulers": [100],
   ```
   > Aligner sur `line-length = 100` de Ruff.

3. **Ajouter `.vscode/extensions.json`** avec les extensions essentielles :
   ```json
   {
     "recommendations": [
       "ms-python.python",
       "ms-python.pylance",
       "ms-python.vscode-pylance",
       "charliermarsh.ruff",
       "cython.cython"
     ]
   }
   ```

- [x] Modifier `.gitignore` (remplacer `.vscode/` par `.vscode/*.code-workspace`)
- [x] Corriger `"editor.rulers": [100]` dans `.vscode/settings.json`
- [x] Créer `.vscode/extensions.json`
- [x] `git add .vscode/settings.json .vscode/extensions.json`

---

### P1-2 · Supprimer le chemin Windows absolu de `copilot-instructions.md`

**Fichier :** `.github/copilot-instructions.md`  
**Risque :** `c:\Users\averr\MULTI_ASSETS` est machine-spécifique et expose le nom d'utilisateur
local dans un fichier potentiellement public. La CI Ubuntu lit ce fichier et le chemin est incorrect.

**Action — modifier la section Stack :**
```markdown
# Avant
- Venv: `.venv/` · Tests: `pytest` depuis `c:\Users\averr\MULTI_ASSETS`

# Après
- Venv: `.venv/` · Tests: `pytest tests/ -x -q` (depuis la racine du repo)
```

- [x] Modifier `.github/copilot-instructions.md`

---

### P1-3 · Résoudre la duplication `pytest.ini` / `pyproject.toml`

**Fichiers :** `pytest.ini`, `pyproject.toml`  
**Risque :** `pytest.ini` a la priorité sur `[tool.pytest.ini_options]` selon la doc pytest.
Le `filterwarnings` de `pytest.ini` supprime **tous** les `DeprecationWarning` globalement,
masquant les avertissements de `python-binance`, `pandas`, et autres dépendances critiques.

**Actions :**

1. **Supprimer `pytest.ini`** en migrant son contenu dans `pyproject.toml` :
   ```toml
   # Dans pyproject.toml, remplacer le bloc existant :
   [tool.pytest.ini_options]
   testpaths = ["tests"]
   
   # Par :
   [tool.pytest.ini_options]
   testpaths = ["tests"]
   filterwarnings = [
       "ignore::ResourceWarning",
       # DeprecationWarning intentionnellement non-supprimé — surveiller les régressions API
   ]
   ```

2. **Supprimer le fichier `pytest.ini`** :
   ```powershell
   git rm pytest.ini
   ```

- [x] Migrer `filterwarnings` dans `pyproject.toml` (retirer DeprecationWarning)
- [x] `git rm pytest.ini`
- [x] Vérifier : `pytest tests/ -x -q` — aucun test cassé

---

### P1-4 · Ajouter Ruff et Pyright à la CI

**Fichier :** `.github/workflows/main.yml`  
**Risque :** La CI ne fait qu'exécuter pytest. Les erreurs Ruff et Pyright ne bloquent jamais
une PR/push — le static analysis est purement local et déclaratif.

**Action — ajouter deux steps après `Install dependencies` :**
```yaml
      - name: Lint (Ruff)
        run: |
          pip install ruff
          ruff check code/src/ tests/

      - name: Type check (Pyright)
        run: |
          pip install pyright
          pyright --project pyrightconfig.json
```

- [x] Modifier `.github/workflows/main.yml`
- [x] Vérifier en local que `ruff check code/src/ tests/` passe sans erreur bloquante
- [x] Vérifier en local que `pyright --project pyrightconfig.json` passe

---

### P1-5 · Référencer les fichiers `.context.md` dans le fichier AI central

**Fichiers :** `.github/copilot-instructions.md`, `code/src/*.context.md`  
**Risque :** Les cinq fichiers `.context.md` dans `code/src/` (`backtest_runner`,
`exchange_client`, `MULTI_SYMBOLS`, `state_manager`, `walk_forward`) contiennent des
contraintes critiques mais sont invisibles pour un agent IA qui lit copilot-instructions.

**Action — ajouter une section dans `.github/copilot-instructions.md`** :
```markdown
## Contexte modulaire

Chaque module critique dispose d'un fichier de contexte dans `code/src/` :
- `backtest_runner.context.md` — contraintes du moteur backtest
- `exchange_client.context.md` — règles du client Binance
- `MULTI_SYMBOLS.context.md` — architecture de l'orchestrateur
- `state_manager.context.md` — format état + HMAC
- `walk_forward.context.md` — OOS gates + métriques

Consulter le fichier `.context.md` du module concerné avant toute modification.
```

- [x] Ajouter la section dans `.github/copilot-instructions.md`

---

## SPRINT 2 — P2 Améliorations

---

### P2-1 · Compléter `.gitignore` avec les caches d'outils

**Fichier :** `.gitignore`  
**Action — ajouter après la section `# Couverture de code` :**
```gitignore
# Caches d'outils d'analyse statique
.mypy_cache/
.pytest_cache/
.ruff_cache/
```

- [x] Modifier `.gitignore`
- [x] Vérifier : `git status` ne montre plus ces dossiers comme untracked

---

### P2-2 · Rendre `agents/` et `architecture/` visibles depuis le README

**Fichier :** `README.md`  
**Action — ajouter une section `## Ressources AI & Architecture`** après `## Tests` :
```markdown
## Ressources AI & Architecture

| Fichier | Rôle |
|---------|------|
| `.github/copilot-instructions.md` | Contexte principal pour GitHub Copilot |
| `.claude/context.md` | Contexte principal pour Claude |
| `.claude/rules.md` | Règles de modification + priorités |
| `agents/code_auditor.md` | Agent spécialisé audit sécurité/concurrence |
| `agents/quant_engineer.md` | Agent spécialisé backtest/signaux |
| `agents/risk_manager.md` | Agent spécialisé gestion du risque |
| `architecture/decisions.md` | Décisions d'architecture documentées |
| `architecture/system_design.md` | Design système global |
```

- [x] Modifier `README.md`

---

### P2-3 · Corriger le comptage des tests dans `copilot-instructions.md`

**Fichier :** `.github/copilot-instructions.md`  
**Action :**
```markdown
# Avant
- Venv: `.venv/` · Tests: `pytest` depuis `c:\Users\averr\MULTI_ASSETS`

# Après (combiner avec P1-2)
- Venv: `.venv/` · Tests: `pytest tests/ -x -q` (78+ tests, 25 fichiers)
```

> Ce correctif est inclus dans P1-2 si les deux items sont traités ensemble.

- [x] Vérifier le décompte exact : `pytest tests/ --collect-only -q 2>&1 | tail -3`
- [x] Mettre à jour le chiffre dans `.github/copilot-instructions.md`

---

### P2-4 · Créer `.vscode/tasks.json` pour les commandes fréquentes

**Fichier à créer :** `.vscode/tasks.json`  
**Action :** Créer le fichier avec les tâches documentées dans `copilot-instructions.md` :
```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "pytest: tout (rapide)",
      "type": "shell",
      "command": "${workspaceFolder}/.venv/Scripts/python.exe -m pytest tests/ -x -q",
      "group": { "kind": "test", "isDefault": true },
      "presentation": { "reveal": "always", "panel": "shared" }
    },
    {
      "label": "ruff: vérifier code/src",
      "type": "shell",
      "command": "${workspaceFolder}/.venv/Scripts/python.exe -m ruff check code/src/",
      "group": "build"
    },
    {
      "label": "pyright: type check",
      "type": "shell",
      "command": "${workspaceFolder}/.venv/Scripts/python.exe -m pyright --project pyrightconfig.json",
      "group": "build"
    }
  ]
}
```

- [x] Créer `.vscode/tasks.json`

---

### P2-5 · Archiver ou annoter les fichiers `tasks/` comme travaux terminés

**Fichiers :** `tasks/audit_email_alerts.md`, `tasks/audit_system.md`, `tasks/correct_p0.md`  
**Risque :** Un agent IA les lit comme des issues ouvertes et génère des modifications non demandées.

**Action :** Ajouter en en-tête de chaque fichier encore actif le statut explicite :
```markdown
> **Statut** : ✅ Résolu le JJ/MM/AAAA — conserver pour référence historique
```
Pour les tâches non encore résolues, ajouter :
```markdown
> **Statut** : 🔄 En cours — ne pas modifier sans validation préalable
```

- [x] Annoter `tasks/audit_email_alerts.md`
- [x] Annoter `tasks/audit_system.md`
- [x] Annoter `tasks/correct_p0.md`

---

### P2-6 · Documenter demo vs production dans le README

**Fichier :** `README.md`  
**Action — ajouter une section `## Modes d'exécution`** :
```markdown
## Modes d'exécution

| Mode | Commande | Effet |
|------|----------|-------|
| **Backtest seul** | `python code/src/backtest_runner.py` | Aucun ordre réel, aucune connexion Binance requise |
| **Live (direct)** | `cd code/src && python MULTI_SYMBOLS.py` | Ordres réels — clés API actives obligatoires |
| **Production (PM2)** | `pm2 start config/ecosystem.config.js` | Démon supervisé, redémarrage automatique |

> ⚠️ En mode Live ou Production, toute position ouverte engage du capital réel.  
> Vérifier `BINANCE_API_KEY` et `BINANCE_SECRET_KEY` dans `.env` avant tout démarrage.
```

- [x] Modifier `README.md`

---

### P2-7 · Vérifier la couverture du gitignore pour `code/src/states/`

**Fichier :** `.gitignore`  
**Risque :** Le `.gitignore` contient `states/` et `code/states/` mais le chemin runtime
réel est `code/src/states/` (visible dans le listing `code/src/`).

**Vérification :**
```powershell
git ls-files code/src/states/
```
Si des fichiers remontent :
```powershell
git rm --cached -r code/src/states/
```

**Action — ajouter dans `.gitignore`** :
```gitignore
code/src/states/
code/src/logs/
code/src/cache/
```

- [x] Exécuter `git ls-files code/src/states/` et nettoyer si nécessaire
- [x] Ajouter les trois patterns dans `.gitignore`

---

## Validation finale

Après l'ensemble des sprints, exécuter la séquence de validation complète :

```powershell
# 1 — Vérifier qu'aucun secret ou CSV n'est tracké
git ls-files "*.env" "*.csv" "*.meta.json" states/ code/src/states/

# 2 — Vérifier la syntaxe de tous les modules modifiés
.venv\Scripts\python.exe -c "import ast, pathlib; [ast.parse(f.read_text()) for f in pathlib.Path('code/src').glob('*.py')]; print('Syntaxe OK')"

# 3 — Ruff (lint)
.venv\Scripts\python.exe -m ruff check code/src/ tests/

# 4 — Pyright (types)
.venv\Scripts\python.exe -m pyright --project pyrightconfig.json

# 5 — Pytest (suite complète)
pytest tests/ -x -q

# 6 — Vérifier la cohérence de version Python
python --version
```

**Critères de succès :**
- [x] Étape 1 retourne zéro fichier
- [x] Étapes 2-4 passent sans erreur bloquante
- [x] Étape 5 : 0 failed, 0 error
- [x] Étape 6 : `Python 3.13.x`

---

## Récapitulatif des fichiers modifiés

| Fichier | Sprints | Type |
|---------|---------|------|
| `README.md` | P0-1, P0-2, P2-2, P2-6 | Modification |
| `.gitignore` | P0-3, P2-1, P2-7 | Modification |
| `requirements.txt` | P0-2 | Modification |
| `.github/workflows/main.yml` | P0-2, P1-4 | Modification |
| `pyproject.toml` | P0-2, P1-3 | Modification |
| `pytest.ini` | P1-3 | Suppression |
| `.vscode/settings.json` | P1-1 | Modification |
| `.vscode/extensions.json` | P1-1 | Création |
| `.vscode/tasks.json` | P2-4 | Création |
| `.github/copilot-instructions.md` | P1-2, P1-5, P2-3 | Modification |
| `tasks/*.md` | P2-5 | Annotation |
