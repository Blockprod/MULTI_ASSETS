# SCAN_result — MULTI_ASSETS

**Date** : 2026-04-06 21:14  
**Périmètre** : `code/src/*.py` (30 fichiers) · `tests/*.py`  
**Pyright config** : `pyrightconfig.json` — typeCheckingMode: "off"  
**Baseline tests** : 739 passed · 7 skipped · 0 failed

---

## TOTAUX

| Outil | Périmètre | Violations |
|-------|-----------|-----------|
| ruff F401 (imports inutilisés) | `code/src/` | **3** |
| ruff ARG001 (args inutilisés) | `code/src/` | **6** |
| ruff F401 (imports inutilisés) | `tests/` | **4** |
| pyright (reportMissingImports + reportUndefinedVariable) | `code/src/` | **0** |
| IDE get_errors | `code/src/` + `tests/` | **0** |
| `# type: ignore` (interdit absolu) | `code/src/` | **1** |
| `datetime.utcnow()` | `code/src/` | **0** |
| `TRAILING_STOP_MARKET` (usage réel invalide) | `code/src/` | **0** ✅ compliant |
| `start_date` figée à l'import | `code/src/` | **0** |
| `except Exception: pass` muet | `code/src/` | **20** |
| `print()` en production | `code/src/` | **2** |
| **TOTAL actionnable** | | **36** |

> Note : les 5 occurrences de `TRAILING_STOP_MARKET` dans `exchange_client.py` et `MULTI_SYMBOLS.py`
> sont toutes dans des gardes `raise NotImplementedError` — conformes aux règles Spot.
> Note : `code/scripts/p003_functions.py` contient 118 erreurs IDE — fichier legacy hors périmètre.

---

## FILES_TO_FIX

```yaml
FILES_TO_FIX:

  - file: "code/src/cache_manager.py"
    errors: ["silent_except"]
    count: 9
    lines: [89, 97, 107, 145, 148, 181, 187, 266, 276]
    priority: P1

  - file: "code/src/MULTI_SYMBOLS.py"
    errors: ["ruff_F401", "ARG001", "silent_except"]
    count: 4
    lines:
      ruff_F401: [89, 156]
      ARG001: [1632]
      silent_except: [165]
    priority: P1

  - file: "code/src/exchange_client.py"
    errors: ["ARG001", "silent_except", "print_prod"]
    count: 4
    lines:
      ARG001: [708]
      silent_except: [422]
      print_prod: [262, 268]
    priority: P1

  - file: "code/src/indicators_engine.py"
    errors: ["silent_except"]
    count: 2
    lines: [277, 375]
    priority: P1

  - file: "code/src/data_fetcher.py"
    errors: ["silent_except"]
    count: 2
    lines: [251, 300]
    priority: P1

  - file: "code/src/backtest_orchestrator.py"
    errors: ["ARG001"]
    count: 2
    lines: [461, 462]
    priority: P2

  - file: "code/src/watchdog.py"
    errors: ["ARG001"]
    count: 2
    lines: [49, 49]
    note: "Les 2 except Exception: pass à L44+L59 sont intentionnels et commentés — non à corriger"
    priority: P2

  - file: "code/src/order_manager.py"
    errors: ["ruff_F401"]
    count: 1
    lines: [34]
    priority: P2

  - file: "code/src/backtest_runner.py"
    errors: ["silent_except"]
    count: 1
    lines: [406]
    priority: P2

  - file: "code/src/metrics.py"
    errors: ["silent_except"]
    count: 1
    lines: [113]
    priority: P2

  - file: "code/src/position_reconciler.py"
    errors: ["silent_except"]
    count: 1
    lines: [99]
    priority: P2

  - file: "code/src/state_manager.py"
    errors: ["silent_except"]
    count: 1
    lines: [181]
    priority: P2

  - file: "code/src/timestamp_utils.py"
    errors: ["silent_except"]
    count: 1
    lines: [273]
    priority: P2

  - file: "code/src/walk_forward.py"
    errors: ["type_ignore"]
    count: 1
    lines: [723]
    note: "# type: ignore[name-defined] sur optuna.Trial — remplacer par typing explicite"
    priority: P2

  - file: "tests/test_watchdog.py"
    errors: ["ruff_F401"]
    count: 2
    lines: [415, 430]
    priority: P3

  - file: "tests/test_p0_fixes.py"
    errors: ["ruff_F401"]
    count: 1
    lines: [559]
    priority: P3

  - file: "tests/test_phase1_fixes.py"
    errors: ["ruff_F401"]
    count: 1
    lines: [471]
    priority: P3
```

---

## VIOLATIONS_SPECIFIQUES

```yaml
VIOLATIONS_SPECIFIQUES:
  type_ignore:
    - "code/src/walk_forward.py:723  # type: ignore[name-defined]"

  utcnow:        "aucun"
  trailing_stop: "aucun (5 occurrences = gardes NotImplementedError conformes)"
  start_date_figee: "aucun"

  silent_except:
    - "code/src/cache_manager.py:89,97,107,145,148,181,187,266,276"
    - "code/src/MULTI_SYMBOLS.py:165"
    - "code/src/exchange_client.py:422"
    - "code/src/indicators_engine.py:277,375"
    - "code/src/data_fetcher.py:251,300"
    - "code/src/backtest_runner.py:406"
    - "code/src/metrics.py:113"
    - "code/src/position_reconciler.py:99"
    - "code/src/state_manager.py:181"
    - "code/src/timestamp_utils.py:273"
    # Note: watchdog.py:44,59 = intentionnel + commenté → exclus

  print_prod:
    - "code/src/exchange_client.py:262  # dans boucle retry rate-limit → remplacer par logger.debug"
    - "code/src/exchange_client.py:268  # dans boucle retry rate-limit → remplacer par logger.debug"
    # benchmark.py + preload_data.py : intentionnel (scripts utilitaires)
```

---

## FICHIERS PROPRES (0 violation)

```
code/src/bot_config.py
code/src/constants.py
code/src/exceptions.py
code/src/signal_generator.py
code/src/market_analysis.py
code/src/trade_helpers.py
code/src/trade_journal.py
code/src/position_sizing.py
code/src/display_ui.py
code/src/email_utils.py
code/src/email_templates.py
code/src/error_handler.py
code/src/indicators.py
code/src/cython_integrity.py
code/src/preload_data.py   (print intentionnel dans script)
code/src/benchmark.py      (print intentionnel dans script)
```

---

## RÉSUMÉ ACTIONNABLE

| Priorité | Fichiers | Violations |
|----------|----------|-----------|
| **P1** (critique) | cache_manager, MULTI_SYMBOLS, exchange_client, indicators_engine, data_fetcher | **17** |
| **P2** (important) | backtest_orchestrator, watchdog, order_manager, backtest_runner, metrics, position_reconciler, state_manager, timestamp_utils, walk_forward | **12** |
| **P3** (info) | tests/ uniquement | **4** |
| **HORS SCOPE** | watchdog:44,59 (intentionnel commenté) | 2 |

**→ Passer à P2 (génération du plan de correction depuis ce fichier)**
