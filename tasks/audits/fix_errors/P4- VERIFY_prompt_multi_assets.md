---
id: P4-VERIFY
title: "MULTI_ASSETS — Vérification post-correction"
creation: 2026-04-06 à 21:14
phase: 4
depends_on: P3-FIX_core
output: tasks/audits/fix_errors/fix_results/VERIFY_result.md
---

# P4 · Vérification post-correction — MULTI_ASSETS

## Contexte

Ce prompt exécute la vérification complète après application des corrections P3. Il couvre :
- Qualité statique (ruff, pyright)
- Suite de tests (739+ passes, 0 failed)
- Intégrité de configuration
- Intégrité HMAC de l'état persisté
- Grep des interdictions absolues (aucune régression)

---

## Pré-requis

- Environnement : `.venv/` activé au niveau du repo
- Répertoire : `C:\Users\averr\MULTI_ASSETS`
- Corrections P3 appliquées et fichiers Python syntaxiquement valides

---

## Étape 1 — Validation syntaxique de tous les fichiers modifiés

Pour chaque fichier `.py` modifié pendant P3 :

```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/<fichier>.py').read()); print('OK')"
```

Critère : aucun `SyntaxError`. Si un fichier échoue, retourner en P3.

---

## Étape 2 — Ruff : qualité statique

```powershell
# Check général (E, W, F, I, B, C, UP, N, S, ANN, PIE, RET, SIM...)
.venv\Scripts\python.exe -m ruff check code/src/

# Check ARG (arguments inutilisés)
.venv\Scripts\python.exe -m ruff check code/src/ --select ARG

# Check format
.venv\Scripts\python.exe -m ruff format --check code/src/
```

Seuil : **0 violation ruff** (E, W, F, B, ARG). Warnings ANN tolérés si non régressifs.

Si des violations subsistent : documenter dans `VERIFY_result.md` avec le count exact par code d'erreur.

---

## Étape 3 — Pyright : vérification de types

```powershell
# Check global
.venv\Scripts\python.exe -m pyright --project pyrightconfig.json

# Check fichier par fichier pour les modules modifiés
.venv\Scripts\python.exe -m pyright code/src/<fichier>.py
```

Seuil : **0 erreur pyright**. Les warnings sont tolérés.

Note : `pyrightconfig.json` à la racine est la config de référence. `typeCheckingMode` peut être `"off"` ou `"basic"`.

---

## Étape 4 — Suite de tests complète

```powershell
.venv\Scripts\python.exe -m pytest tests/ -x -q
```

Seuil minimum : **739 passed, 0 failed** (7 skipped autorisés — testnet + data-dependent).

En cas d'échec :
- Lire l'output complet du test en erreur
- Si P3 a introduit la régression : retourner en P3 avec le traceback
- Si le test était déjà cassé avant P3 : documenter dans `VERIFY_result.md` et justifier

---

## Étape 5 — Vérification de configuration

### 5a. Variables d'environnement critiques (structure uniquement — ne pas logger les valeurs)
```powershell
# Vérifier que la classe Config charge sans erreur
.venv\Scripts\python.exe -c "
import os
os.environ.setdefault('BINANCE_API_KEY', 'test_key')
os.environ.setdefault('BINANCE_SECRET_KEY', 'test_secret_key')
os.environ.setdefault('SENDER_EMAIL', 'test@test.com')
os.environ.setdefault('RECEIVER_EMAIL', 'test@test.com')
os.environ.setdefault('GOOGLE_MAIL_PASSWORD', 'test_pass')
from bot_config import Config
c = Config.from_env()
print('Config OK')
print('BOT_MODE:', os.environ.get('BOT_MODE', 'NON DEFINI'))
"
```

Critère : `Config OK` sans exception.

### 5b. Vérification BOT_MODE
Valeurs autorisées : `LIVE`, `DEMO`, `PAPER`.  
Valeurs interdites : `production`, `test`, `prod`.

### 5c. Cohérence des paramètres de risque
```powershell
.venv\Scripts\python.exe -c "
import os
os.environ.setdefault('BINANCE_API_KEY', 'test_key')
os.environ.setdefault('BINANCE_SECRET_KEY', 'test_secret_key')
os.environ.setdefault('SENDER_EMAIL', 'test@test.com')
os.environ.setdefault('RECEIVER_EMAIL', 'test@test.com')
os.environ.setdefault('GOOGLE_MAIL_PASSWORD', 'test_pass')
from bot_config import Config
c = Config.from_env()
assert 0 < c.stop_loss_pct < 0.10, f'stop_loss_pct hors plage: {c.stop_loss_pct}'
assert 0 < c.risk_per_trade <= 0.05, f'risk_per_trade hors plage: {c.risk_per_trade}'
assert c.daily_loss_limit_pct == 0.05, f'daily_loss_limit_pct altéré: {c.daily_loss_limit_pct}'
assert c.taker_fee == 0.0007, f'taker_fee altéré: {c.taker_fee}'
assert c.maker_fee == 0.0002, f'maker_fee altéré: {c.maker_fee}'
print('Cohérence risque OK')
"
```

---

## Étape 6 — Intégrité HMAC de l'état persisté

```powershell
.venv\Scripts\python.exe -c "
import os, sys
sys.path.insert(0, 'code/src')
os.environ.setdefault('BINANCE_API_KEY', 'test_key')
os.environ.setdefault('BINANCE_SECRET_KEY', 'test_secret_key')
os.environ.setdefault('SENDER_EMAIL', 'test@test.com')
os.environ.setdefault('RECEIVER_EMAIL', 'test@test.com')
os.environ.setdefault('GOOGLE_MAIL_PASSWORD', 'test_pass')
from state_manager import _compute_state_hash
import json
data = {'test': 'payload', '_state_version': 'JSON_V1'}
h = _compute_state_hash(data, 'test_secret_key')
assert isinstance(h, str) and len(h) == 64, 'HMAC invalide'
print('HMAC state_manager OK')
"
```

Critère : `HMAC state_manager OK` sans exception.

---

## Étape 7 — Import des modules critiques

```powershell
.venv\Scripts\python.exe -c "
import sys; sys.path.insert(0, 'code/src')
import importlib
modules = [
    'bot_config', 'state_manager', 'exchange_client', 'order_manager',
    'signal_generator', 'position_sizing', 'trade_helpers', 'trade_journal',
    'backtest_runner', 'walk_forward', 'data_fetcher', 'cache_manager',
    'error_handler', 'metrics', 'watchdog', 'timestamp_utils', 'constants',
    'exceptions', 'indicators_engine', 'market_analysis'
]
for m in modules:
    importlib.import_module(m)
    print(f'  {m} OK')
print('Tous les imports OK')
"
```

Critère : aucun `ImportError` ou `ModuleNotFoundError`.

---

## Étape 8 — Import Cython (code/bin/)

```powershell
.venv\Scripts\python.exe -c "
import sys; sys.path.insert(0, 'code/bin')
from backtest_engine_standard import run_backtest
from indicators import compute_indicators
print('Cython imports OK')
"
```

Critère : `Cython imports OK`. Ne jamais recompiler les .pyd — ils sont pré-compilés.

---

## Étape 9 — Grep des interdictions absolues

```powershell
# type: ignore → interdit
Select-String -Path "code\src\*.py" -Pattern "# type: ignore" | Select-Object Path, LineNumber, Line

# utcnow() → interdit (datetime.timezone.utc requis)
Select-String -Path "code\src\*.py" -Pattern "datetime\.utcnow\(\)" | Select-Object Path, LineNumber, Line

# TRAILING_STOP_MARKET → interdit sur Spot
Select-String -Path "code\src\*.py" -Pattern "TRAILING_STOP_MARKET" | Select-Object Path, LineNumber, Line

# except muet → interdit
Select-String -Path "code\src\*.py" -Pattern "except\s+Exception\s*:\s*pass" | Select-Object Path, LineNumber, Line
Select-String -Path "code\src\*.py" -Pattern "except\s*:\s*pass" | Select-Object Path, LineNumber, Line

# start_date figée à l'import → interdit
Select-String -Path "code\src\*.py" -Pattern "start_date\s*=\s*[""']20\d\d" | Select-Object Path, LineNumber, Line

# bot_state modifié sans lock → à vérifier manuellement
Select-String -Path "code\src\MULTI_SYMBOLS.py" -Pattern "bot_state\[" | Select-Object LineNumber, Line
```

Seuil : **0 occurrence** pour chaque pattern. Si des hits existent provenant de P3 : retourner en P3.

---

## Étape 10 — Thread safety des écritures bot_state

Vérifier manuellement que toutes les occurrences de `bot_state["..."] =` dans `MULTI_SYMBOLS.py` sont à l'intérieur d'un bloc `with _bot_state_lock:`.

```powershell
Select-String -Path "code\src\MULTI_SYMBOLS.py" -Pattern "_bot_state_lock" | Measure-Object | Select-Object Count
```

Critère : le nombre d'acquisitions du lock doit être cohérent avec le nombre de sections d'écriture.

---

## Sortie attendue

Produire le fichier `tasks/audits/fix_errors/fix_results/VERIFY_result.md` avec :

```markdown
# VERIFY_result — MULTI_ASSETS

**Date** : YYYY-MM-DD HH:MM
**Corrections P3 appliquées** : <liste des fichiers modifiés>

## Résultats

| Étape | Verdict | Notes |
|-------|---------|-------|
| 1. Syntaxe | ✅ PASS / ❌ FAIL | |
| 2. Ruff | ✅ PASS / ❌ FAIL | N violations |
| 3. Pyright | ✅ PASS / ❌ FAIL | N erreurs |
| 4. Tests | ✅ PASS / ❌ FAIL | N passed, N failed |
| 5. Config | ✅ PASS / ❌ FAIL | |
| 6. HMAC | ✅ PASS / ❌ FAIL | |
| 7. Imports | ✅ PASS / ❌ FAIL | |
| 8. Cython | ✅ PASS / ❌ FAIL | |
| 9. Interdictions | ✅ PASS / ❌ FAIL | N occurrences |
| 10. Thread safety | ✅ PASS / ❌ FAIL | |

## Verdict global

**✅ TOUTES LES ÉTAPES PASSÉES → Passer à P5-FINAL QA**

OU

**❌ ÉCHEC(S) détecté(s) → Retourner en P3 avec les détails ci-dessus**
```

---

## Règle de passage à P5

Tous les critères suivants doivent être verts simultanément :
- 0 erreur ruff E/W/F/B/ARG
- 0 erreur pyright
- ≥ 739 passed, **0 failed**
- 0 occurrence des interdictions absolues
- Config et HMAC OK
- Tous les imports OK

Si un seul critère est rouge → retour obligatoire en P3.
