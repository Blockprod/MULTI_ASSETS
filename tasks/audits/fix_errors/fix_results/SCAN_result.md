# SCAN_result — MULTI_ASSETS

**Date** : 2026-05-04 à 11:01 _(scan précédent : 2026-04-14)_  
**Périmètre** : `code/src/*.py` (30 fichiers) · `tests/*.py`  
**Pyright config** : `pyrightconfig.json` — typeCheckingMode: "basic"  
**Baseline tests** : 807 passed · 0 failed

---

## TOTAUX

| Outil | Périmètre | Violations |
|-------|-----------|-----------|
| ruff général | `code/src/` | **0** ✅ |
| ruff ARG (args inutilisés) | `code/src/` | **0** ✅ |
| ruff | `tests/` | **0** ✅ |
| pyright (basic) | `code/src/` 30 fichiers | **0** ✅ |
| IDE get_errors | `code/src/` + `tests/` | **0** ✅ |
| `# type: ignore` | `code/src/` + `tests/` | **0** ✅ |
| `datetime.utcnow()` | `code/src/` | **0** ✅ |
| `TRAILING_STOP_MARKET` (usage réel invalide) | `code/src/` | **0** ✅ guards OK |
| `start_date` figée à l'import | `code/src/` | **0** ✅ |
| `except Exception: pass` muet | `code/src/` | **0** ✅ |
| `print()` en production | `code/src/` runtime | **0** ✅ |
| **TOTAL actionnable** | | **0** 🟢 |

---

## FILES_TO_FIX

```yaml
FILES_TO_FIX: []  # Aucune violation détectée — codebase 100% propre
```

---

## VIOLATIONS_SPECIFIQUES

```
type_ignore      : aucun
utcnow           : aucun
trailing_stop    : aucun appel effectif (exchange_client.py:571 dans fonction compatibility
                   non-atteignable Spot — MULTI_SYMBOLS.py:400 raise NotImplementedError ✅)
start_date_figee : aucun
silent_except    : aucun
print_prod       : aucun en runtime (benchmark.py + preload_data.py = scripts CLI exclus)
```

---

## FICHIERS PROPRES (code/src/ — 30/30)

Tous les fichiers `code/src/` sont propres (0 violation ruff + 0 erreur pyright) :

```
bot_config.py, constants.py, exceptions.py, state_manager.py, metrics.py,
exchange_client.py, timestamp_utils.py, order_manager.py, position_reconciler.py,
backtest_orchestrator.py, data_fetcher.py, indicators_engine.py, signal_generator.py,
market_analysis.py, backtest_runner.py, walk_forward.py, trade_helpers.py,
trade_journal.py, position_sizing.py, cache_manager.py, display_ui.py,
email_utils.py, email_templates.py, error_handler.py, MULTI_SYMBOLS.py,
watchdog.py, indicators.py, cython_integrity.py, benchmark.py, preload_data.py
```

---

## RÉSUMÉ ACTIONNABLE

| Priorité | Fichiers | Violations |
|----------|----------|-----------|
| — | — | — |
| **TOTAL** | **0 fichier** | **0** 🟢 |

**→ Aucune action requise. Codebase entièrement propre au 2026-05-04.**

_Violations résolues depuis le scan 2026-04-14 :_
- `ARG001` watchdog.py:49 ✅ corrigé
- `# type: ignore` tests/test_e2e_testnet.py:93,102 ✅ corrigé
- `# type: ignore` tests/test_indicators_consistency.py:301 ✅ corrigé

