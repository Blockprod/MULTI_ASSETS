---
id: P5-FINAL-QA
title: "MULTI_ASSETS — QA finale avant déploiement"
creation: 2026-04-06 à 21:14
phase: 5
depends_on: P4-VERIFY
output: tasks/audits/fix_errors/fix_results/FINAL_QA_result.md
---

# P5 · QA Finale avant déploiement — MULTI_ASSETS

## Contexte

Ce prompt est la dernière gate avant de considérer un cycle de correction comme terminé. Il ordonne une checklist exhaustive orientée production : pipeline complet, cohérence stratégique, sécurité, état persisté, supervision PM2.

P5 ne se lance que si P4 a retourné **TOUTES LES ÉTAPES PASSÉES**.

---

## Checklist finale (10 points)

### ✅ Point 1 — Qualité statique globale (regression check)

```powershell
.venv\Scripts\python.exe -m ruff check code/src/ --statistics
.venv\Scripts\python.exe -m pyright --project pyrightconfig.json 2>&1 | Select-String "error|warning" | Measure-Object
```

Attendu : **0 erreur ruff**, **0 erreur pyright**.  
Documenter le nombre de warnings pyright (toléré si stable ou en baisse).

---

### ✅ Point 2 — Suite de tests (avec DeprecationWarning)

```powershell
.venv\Scripts\python.exe -m pytest tests/ -x -q -W error::DeprecationWarning 2>&1
```

Si des `DeprecationWarning` apparaissent : les corriger avant de marquer ce point vert.  
Attendu : **≥ 739 passed, 0 failed, 0 DeprecationWarning** (7 skipped autorisés).

---

### ✅ Point 3 — Cohérence de configuration production

```powershell
.venv\Scripts\python.exe -c "
import os, sys
sys.path.insert(0, 'code/src')
os.environ.setdefault('BINANCE_API_KEY', 'test_key')
os.environ.setdefault('BINANCE_SECRET_KEY', 'test_secret_key')
os.environ.setdefault('SENDER_EMAIL', 'test@test.com')
os.environ.setdefault('RECEIVER_EMAIL', 'test@test.com')
os.environ.setdefault('GOOGLE_MAIL_PASSWORD', 'test_pass')
from bot_config import Config
c = Config.from_env()

# Fees jamais modifiés
assert c.taker_fee == 0.0007
assert c.maker_fee == 0.0002
assert c.backtest_taker_fee == 0.001
assert c.backtest_maker_fee == 0.001

# Protections capital
assert c.daily_loss_limit_pct == 0.05
assert 0 < c.stop_loss_pct < 0.10
assert 0 < c.risk_per_trade <= 0.05

# recvWindow
assert c.recv_window == 60000

# Suppression repr clair de l'API key
r = repr(c)
assert 'test_key' not in r, 'api_key exposé dans repr!'
assert 'test_secret_key' not in r, 'secret_key exposé dans repr!'

print('Config production OK')
"
```

---

### ✅ Point 4 — Import Cython + smoke test backtest

```powershell
.venv\Scripts\python.exe -c "
import sys
sys.path.insert(0, 'code/bin')
sys.path.insert(0, 'code/src')
from backtest_engine_standard import run_backtest
from indicators import compute_indicators
print('Cython binaries OK')
"
```

Vérifier également que `code/bin/*.pyd` existent et n'ont PAS été écrasés par une recompilation :
```powershell
Get-ChildItem code\bin\ -Filter *.pyd | Select-Object Name, LastWriteTime
```

Les `.pyd` ne doivent jamais être plus récents que la date de leur dernière validation connue.

---

### ✅ Point 5 — Pipeline signal → backtest (smoke test end-to-end)

```powershell
.venv\Scripts\python.exe -c "
import sys, os
sys.path.insert(0, 'code/src')
sys.path.insert(0, 'code/bin')
os.environ.setdefault('BINANCE_API_KEY', 'test_key')
os.environ.setdefault('BINANCE_SECRET_KEY', 'test_secret_key')
os.environ.setdefault('SENDER_EMAIL', 'test@test.com')
os.environ.setdefault('RECEIVER_EMAIL', 'test@test.com')
os.environ.setdefault('GOOGLE_MAIL_PASSWORD', 'test_pass')

# Import des modules pipeline sans crash
from bot_config import Config
from signal_generator import SignalGenerator
from position_sizing import PositionSizer
from state_manager import load_state, save_state
from trade_helpers import compute_pnl
from metrics import write_metrics, read_metrics

print('Pipeline smoke test OK')
"
```

---

### ✅ Point 6 — Grep complet des interdictions absolues

```powershell
# 1. type: ignore (interdit — voir coding-preferences.md)
$r1 = Select-String -Path "code\src\*.py" -Pattern "# type: ignore"
if ($r1) { Write-Host "❌ type:ignore trouvé"; $r1 } else { Write-Host "✅ type:ignore absent" }

# 2. datetime.utcnow() → risque TZ naive
$r2 = Select-String -Path "code\src\*.py" -Pattern "datetime\.utcnow\(\)"
if ($r2) { Write-Host "❌ utcnow trouvé"; $r2 } else { Write-Host "✅ utcnow absent" }

# 3. TRAILING_STOP_MARKET → n'existe pas sur Spot Binance
$r3 = Select-String -Path "code\src\*.py" -Pattern "TRAILING_STOP_MARKET"
if ($r3) { Write-Host "❌ TRAILING_STOP_MARKET trouvé"; $r3 } else { Write-Host "✅ TRAILING_STOP_MARKET absent" }

# 4. except muet
$r4 = Select-String -Path "code\src\*.py" -Pattern "except\s*(Exception\s*)?:\s*pass"
if ($r4) { Write-Host "❌ except muet trouvé"; $r4 } else { Write-Host "✅ except muet absent" }

# 5. print() dans code/src (hors display_ui.py et benchmark.py)
$r5 = Select-String -Path "code\src\*.py" -Pattern "^\s*print\("  | Where-Object { $_.Path -notmatch "display_ui|benchmark" }
if ($r5) { Write-Host "⚠️  print() trouvé (non bloquant si intentionnel)"; $r5 | Select-Object Path, LineNumber, Line }

# 6. start_date figée à l'import
$r6 = Select-String -Path "code\src\*.py" -Pattern "start_date\s*=\s*[""']20\d\d"
if ($r6) { Write-Host "❌ start_date figée"; $r6 } else { Write-Host "✅ start_date dynamique" }

# 7. backtest_taker_fee modifié au runtime
$r7 = Select-String -Path "code\src\*.py" -Pattern "backtest_taker_fee\s*=" | Where-Object { $_.Path -notmatch "bot_config|backtest_runner" }
if ($r7) { Write-Host "❌ backtest_taker_fee modifié hors config"; $r7 } else { Write-Host "✅ backtest_taker_fee intact" }
```

Seuil : **0 occurrence** pour les points 1–4 et 6–7. Point 5 (print) : documenter mais non bloquant.

---

### ✅ Point 7 — Intégrité de l'état persisté

```powershell
# Vérifier que bot_state.json existe et est lisible
Test-Path "states\bot_state.json"

# Vérifier la signature HMAC sans clé réelle (test structure)
.venv\Scripts\python.exe -c "
import sys, os, json
sys.path.insert(0, 'code/src')
path = 'states/bot_state.json'
if not os.path.exists(path):
    print('bot_state.json absent — état vide attendu au premier démarrage')
else:
    raw = open(path).read()
    if raw.startswith('JSON_V1:'):
        print('Format JSON_V1 OK')
    else:
        print('Format état inconnu — vérifier state_manager.py')
"
```

Critère : `JSON_V1 OK` ou fichier absent (premier démarrage).

---

### ✅ Point 8 — Configuration PM2

```powershell
# Vérifier que ecosystem.config.js existe
Test-Path "config\ecosystem.config.js"

# Vérifier la présence du watchdog dans la config PM2
Select-String -Path "config\ecosystem.config.js" -Pattern "watchdog"

# Vérifier que le script principal est référencé
Select-String -Path "config\ecosystem.config.js" -Pattern "MULTI_SYMBOLS"
```

Critère : `ecosystem.config.js` présent, watchdog et MULTI_SYMBOLS référencés.

---

### ✅ Point 9 — Thread safety finale (vérification manuelle)

Ouvrir `code/src/MULTI_SYMBOLS.py` et vérifier :
1. Toutes les écritures `bot_state[...] =` et `pair_state[...] =` sont dans `with _bot_state_lock:`
2. Toutes les exécutions par paire utilisent `_pair_execution_locks[pair]`
3. `_oos_alert_last_sent` est modifié uniquement dans `with _oos_alert_lock:`

```powershell
# Helper : compter les acquisitions de lock vs sections d'écriture
Select-String -Path "code\src\MULTI_SYMBOLS.py" -Pattern "_bot_state_lock|_pair_execution_locks|_oos_alert_lock" | Measure-Object | Select-Object Count
```

Documenter le count dans `FINAL_QA_result.md`. Si < 10 occurrences : vérification manuelle approfondie requise.

---

### ✅ Point 10 — Checklist sécurité finale

```powershell
# Vérifier que api_key n'est jamais loggé en clair
Select-String -Path "code\src\*.py" -Pattern "logger\.(info|debug|warning|error).*api_key" | Select-Object Path, LineNumber, Line
Select-String -Path "code\src\*.py" -Pattern "logger\.(info|debug|warning|error).*secret_key" | Select-Object Path, LineNumber, Line

# Vérifier recvWindow centralisé (pas de valeur hardcodée)
Select-String -Path "code\src\*.py" -Pattern "recvWindow.*=.*[0-9]" | Where-Object { $_.Line -notmatch "recv_window" } | Select-Object Path, LineNumber, Line

# Vérifier que Config.__repr__ masque les secrets
.venv\Scripts\python.exe -c "
import os, sys
sys.path.insert(0, 'code/src')
os.environ['BINANCE_API_KEY'] = 'SUPER_SECRET_API'
os.environ['BINANCE_SECRET_KEY'] = 'SUPER_SECRET_KEY'
os.environ.setdefault('SENDER_EMAIL', 'test@test.com')
os.environ.setdefault('RECEIVER_EMAIL', 'test@test.com')
os.environ.setdefault('GOOGLE_MAIL_PASSWORD', 'test_pass')
from bot_config import Config
c = Config.from_env()
r = repr(c)
assert 'SUPER_SECRET_API' not in r, 'api_key exposé dans repr!'
assert 'SUPER_SECRET_KEY' not in r, 'secret_key exposé dans repr!'
print('Sécurité repr OK')
"
```

---

## Sortie attendue

Produire le fichier `tasks/audits/fix_errors/fix_results/FINAL_QA_result.md` :

```markdown
# FINAL_QA_result — MULTI_ASSETS

**Date** : YYYY-MM-DD HH:MM
**Cycle de correction** : P3-FIX → P4-VERIFY → P5-FINAL QA
**Fichiers modifiés** : <liste complète depuis P3>

## Résultats checklist

| Point | Description | Verdict | Notes |
|-------|-------------|---------|-------|
| 1 | Qualité statique | ✅ / ❌ | N ruff errors, N pyright errors |
| 2 | Tests + DeprecationWarning | ✅ / ❌ | N passed, N failed |
| 3 | Config production | ✅ / ❌ | |
| 4 | Cython binaries | ✅ / ❌ | |
| 5 | Pipeline smoke test | ✅ / ❌ | |
| 6 | Interdictions absolues | ✅ / ❌ | N violations |
| 7 | État persisté (HMAC) | ✅ / ❌ | |
| 8 | PM2 config | ✅ / ❌ | |
| 9 | Thread safety | ✅ / ❌ | N lock acquisitions |
| 10 | Sécurité secrets | ✅ / ❌ | |

## Verdict final

**✅ READY — Toutes les gates passées. Le cycle de correction est terminé.**

OU

**❌ NOT READY — N point(s) en échec. Retourner en P3/P4 selon les items concernés.**

## Notes post-déploiement (optionnel)
- Surveiller les logs PM2 pendant 15 min après redémarrage
- Vérifier `heartbeat.json` toutes les 5 min pour confirmer l'activité du bot
- Vérifier `metrics/metrics.json` pour confirmer que le monitoring fonctionne
```

---

## Règle de clôture du cycle

**READY** uniquement si **10/10 points verts**.

Si un point est rouge :
- Points 1–6 → retour en **P3-FIX** avec le détail exact
- Points 7–8 → vérification manuelle requise avant redémarrage PM2
- Points 9–10 → retour en **P3-FIX** — risque capital ou sécurité

Après validation complète :
1. Mettre à jour `tasks/lessons.md` avec les patterns d'erreurs rencontrés
2. Archiver `fix_results/` avec la date du cycle
3. Redémarrer via PM2 : `pm2 restart ecosystem.config.js`
