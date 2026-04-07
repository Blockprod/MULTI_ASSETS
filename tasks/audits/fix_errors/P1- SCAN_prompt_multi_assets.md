---
modele: sonnet-4.6
mode: agent
contexte: codebase
produit: tasks/audits/fix_errors/fix_results/SCAN_result.md
derniere_revision: 2026-04-06
creation: 2026-04-06 à 21:10
---

#codebase

Tu es un code quality analyst spécialisé Python / pandas / pyright.
Tu réalises un SCAN COMPLET du projet MULTI_ASSETS sans rien modifier.

─────────────────────────────────────────────
RAISONNEMENT
─────────────────────────────────────────────
Explore d'abord, ne corrige jamais. Chaque commande
doit être lancée et son résultat capturé avant de passer
à la suivante.

─────────────────────────────────────────────
ÉTAPE 1 — OUTILS STATIQUES
─────────────────────────────────────────────
Lancer dans l'ordre (terminal PowerShell, .venv Python 3.11) :

```powershell
# 1. Ruff général — code/src/ uniquement (exclure bin/, cache/, __pycache__)
.venv\Scripts\python.exe -m ruff check code/src/ --exclude code/src/__pycache__,code/src/cache,code/src/states,code/src/logs 2>&1 | Select-Object -Last 15

# 2. Ruff ARG (arguments inutilisés)
.venv\Scripts\python.exe -m ruff check code/src/ --select ARG --exclude code/src/__pycache__,code/src/cache 2>&1 | Select-Object -Last 10

# 3. Ruff tests/
.venv\Scripts\python.exe -m ruff check tests/ --exclude tests/__pycache__ 2>&1 | Select-Object -Last 10

# 4. Pyright global (via pyrightconfig.json)
.venv\Scripts\python.exe -m pyright --project pyrightconfig.json 2>&1 | Select-Object -Last 10

# 5. Pyright fichier par fichier sur code/src/ (pour isoler les erreurs)
$src = @(
  "code/src/bot_config.py",
  "code/src/constants.py",
  "code/src/exceptions.py",
  "code/src/state_manager.py",
  "code/src/metrics.py",
  "code/src/exchange_client.py",
  "code/src/timestamp_utils.py",
  "code/src/order_manager.py",
  "code/src/position_reconciler.py",
  "code/src/backtest_orchestrator.py",
  "code/src/data_fetcher.py",
  "code/src/indicators_engine.py",
  "code/src/signal_generator.py",
  "code/src/market_analysis.py",
  "code/src/backtest_runner.py",
  "code/src/walk_forward.py",
  "code/src/trade_helpers.py",
  "code/src/trade_journal.py",
  "code/src/position_sizing.py",
  "code/src/cache_manager.py",
  "code/src/display_ui.py",
  "code/src/email_utils.py",
  "code/src/email_templates.py",
  "code/src/error_handler.py",
  "code/src/MULTI_SYMBOLS.py",
  "code/src/watchdog.py",
  "code/src/indicators.py",
  "code/src/cython_integrity.py",
  "code/src/benchmark.py",
  "code/src/preload_data.py"
)
foreach ($f in $src) {
  if (Test-Path $f) {
    $e = (.venv\Scripts\python.exe -m pyright $f 2>&1 | Select-String "(\d+) error").Matches[0].Groups[1].Value
    if ($e -and $e -ne "0") { Write-Host "$f : $e erreur(s)" }
  }
}
Write-Host "--- scan pyright terminé ---"
```

─────────────────────────────────────────────
ÉTAPE 2 — GET_ERRORS IDE
─────────────────────────────────────────────
Utiliser l'outil `get_errors` (sans argument = tous les fichiers)
pour croiser avec les PROBLEMS de l'IDE.

─────────────────────────────────────────────
ÉTAPE 3 — VÉRIFICATIONS SPÉCIFIQUES MULTI_ASSETS
─────────────────────────────────────────────

```powershell
# A. Rechercher tout usage de # type: ignore (interdit absolu)
Select-String -Path "code\src\*.py","tests\*.py" -Pattern "# type: ignore" -Recurse `
  -ErrorAction SilentlyContinue | Where-Object { $_.Path -notmatch "__pycache__" }

# B. Rechercher datetime.utcnow() (utiliser datetime.now(timezone.utc) à la place)
Select-String -Path "code\src\*.py" -Pattern "datetime\.utcnow\(\)" `
  -ErrorAction SilentlyContinue | Where-Object { $_.Path -notmatch "__pycache__" }

# C. Rechercher TRAILING_STOP_MARKET (invalide sur Spot Binance)
Select-String -Path "code\src\*.py" -Pattern "TRAILING_STOP_MARKET" `
  -ErrorAction SilentlyContinue | Where-Object { $_.Path -notmatch "__pycache__" }

# D. Rechercher start_date figée à l'import (doit utiliser _fresh_start_date())
Select-String -Path "code\src\*.py" -Pattern "start_date\s*=\s*['\"]20[0-9]{2}" `
  -ErrorAction SilentlyContinue | Where-Object { $_.Path -notmatch "__pycache__" }

# E. Rechercher les silent except (except Exception: pass ou ...)
Select-String -Path "code\src\*.py" -Pattern "except\s+Exception\s*:\s*(pass|\.\.\.)\s*$" `
  -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Path -notmatch "__pycache__" }

# F. Rechercher les print() en production (hors tests/)
Select-String -Path "code\src\*.py" -Pattern "^\s*print\(" `
  -ErrorAction SilentlyContinue | Where-Object { $_.Path -notmatch "__pycache__|benchmark|preload" }
```

─────────────────────────────────────────────
ÉTAPE 4 — CLASSIFICATION
─────────────────────────────────────────────
Pour chaque fichier en erreur, identifier le TYPE :

| Code | Type | Exemple MULTI_ASSETS |
|------|------|----------------------|
| `ruff` | style/import | F401, E501, ARG001 |
| `ARG` | unused param | ARG002, ARG004 |
| `typing` | pyright Series/DataFrame/Dict | `Dict[str, Any]` manquant |
| `Timestamp` | pyright NaTType | `pd.Timestamp(x)` → `Timestamp \| NaTType` |
| `type_ignore` | # type: ignore interdit | à corriger avec typage explicite |
| `import` | import manquant / circulaire | `cast` non importé |
| `cython_sig` | signature .pyi ≠ usage | `code/bin/backtest_engine_standard.pyi` |
| `silent_except` | except: pass muet | toujours logger.debug/warning/error |
| `threading` | accès sans _bot_state_lock | écriture bot_state non protégée |
| `utcnow` | datetime.utcnow() déprécié | → datetime.now(timezone.utc) |

─────────────────────────────────────────────
SORTIE OBLIGATOIRE
─────────────────────────────────────────────
Créer `C:\Users\averr\MULTI_ASSETS\tasks\audits\fix_errors\fix_results\SCAN_result.md` avec :

```
FILES_TO_FIX = [
  {
    file: "code/src/fichier.py",
    errors: ["typing", "ARG"],
    count: N,
    lines: [L1, L2, ...]   ← lignes pyright exactes
  },
  ...
]

VIOLATIONS_SPECIFIQUES:
  type_ignore      : [liste fichiers:ligne ou "aucun"]
  utcnow           : [liste fichiers:ligne ou "aucun"]
  trailing_stop    : [liste fichiers:ligne ou "aucun"]
  start_date_figee : [liste fichiers:ligne ou "aucun"]
  silent_except    : [liste fichiers:ligne ou "aucun"]
  print_prod       : [liste fichiers:ligne ou "aucun"]

TOTAUX:
  ruff      : X violation(s)
  ARG       : X violation(s)
  pyright   : X erreur(s) dans Y fichiers
  violations_specifiques: X
  fichiers_propres: [liste...]
```

Confirmer dans le chat :
"✅ SCAN terminé · ruff: X · pyright: X · violations: X"

─────────────────────────────────────────────
