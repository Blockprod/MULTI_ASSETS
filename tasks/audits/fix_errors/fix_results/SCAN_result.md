# SCAN_result — MULTI_ASSETS

**Date** : 2026-04-14  
**Périmètre** : `code/src/*.py` (31 fichiers) · `tests/*.py`  
**Pyright config** : `pyrightconfig.json` — typeCheckingMode: "basic"  
**Baseline tests** : 768 passed · 0 failed

---

## TOTAUX

| Outil | Périmètre | Violations |
|-------|-----------|-----------|
| ruff général | `code/src/` | **0** |
| ruff ARG001 (args inutilisés) | `code/src/` | **2** (watchdog.py:49) |
| ruff | `tests/` | **0** |
| pyright (basic) | `code/src/` 31 fichiers | **0** |
| IDE get_errors | `code/src/` + `tests/` | **0** (testnet.yml = faux positifs) |
| `# type: ignore` | `tests/` | **3** |
| `datetime.utcnow()` | `code/src/` | **0** |
| `TRAILING_STOP_MARKET` (usage réel invalide) | `code/src/` | **0** ✅ compliant |
| `start_date` figée à l'import | `code/src/` | **0** |
| `except Exception: pass` muet | `code/src/` | **0** |
| `print()` en production | `code/src/` runtime | **0** |
| **TOTAL actionnable** | | **5** |

---

## FILES_TO_FIX

```yaml
FILES_TO_FIX:

  - file: "code/src/watchdog.py"
    errors: ["ARG001"]
    count: 2
    lines: [49]
    detail: "ARG001 — subject et body inutilisés dans _send_email_alert() no-op stub"
    priority: P2

  - file: "tests/test_e2e_testnet.py"
    errors: ["type_ignore"]
    count: 2
    lines: [93, 102]
    detail: "# type: ignore[no-untyped-def] — typer client: Any explicitement"
    priority: P3

  - file: "tests/test_indicators_consistency.py"
    errors: ["type_ignore"]
    count: 1
    lines: [301]
    detail: "# type: ignore[import] — module Cython backtest_engine sans stub complet"
    priority: P3
```

---

## VIOLATIONS_SPECIFIQUES

```
ARG001:
  - code/src/watchdog.py:49  — subject (unused)
  - code/src/watchdog.py:49  — body (unused)
  Function: def _send_email_alert(subject: str, body: str) -> bool:  # no-op stub

type_ignore:
  - tests/test_e2e_testnet.py:93   — # type: ignore[no-untyped-def]
  - tests/test_e2e_testnet.py:102  — # type: ignore[no-untyped-def]
  - tests/test_indicators_consistency.py:301 — # type: ignore[import]

utcnow           : aucun
trailing_stop    : aucun appel effectif (guards NotImplementedError conformes)
start_date_figee : aucun
silent_except    : aucun
print_prod       : aucun en runtime (benchmark.py + preload_data.py = scripts CLI exclus)
```

---

## FICHIERS PROPRES (code/src/ — 31/31)

Tous les fichiers `code/src/` sont propres (0 violation ruff + 0 erreur pyright) :

```
bot_config.py, constants.py, exceptions.py, state_manager.py, metrics.py,
exchange_client.py, timestamp_utils.py, order_manager.py, position_reconciler.py,
backtest_orchestrator.py, data_fetcher.py, indicators_engine.py, signal_generator.py,
market_analysis.py, backtest_runner.py, walk_forward.py, trade_helpers.py,
trade_journal.py, position_sizing.py, cache_manager.py, display_ui.py,
email_utils.py, email_templates.py, error_handler.py, MULTI_SYMBOLS.py,
watchdog.py (ARG uniquement), indicators.py, cython_integrity.py,
benchmark.py, preload_data.py, wal_logger.py
```

---

## RÉSUMÉ ACTIONNABLE

| Priorité | Fichiers | Violations |
|----------|----------|-----------|
| P2 | watchdog.py | 2 ARG001 (no-op stub) |
| P3 | tests/test_e2e_testnet.py, tests/test_indicators_consistency.py | 3 type_ignore |
| **TOTAL** | 3 fichiers | **5** |

**→ Passer à P2 (génération du plan de correction depuis ce fichier)**

