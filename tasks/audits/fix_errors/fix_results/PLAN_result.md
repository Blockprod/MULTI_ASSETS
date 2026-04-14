# PLAN_result — MULTI_ASSETS

**Date** : 2026-04-14 22:31  
**Source** : `SCAN_result.md` (5 violations · 3 fichiers · 0 erreur pyright · 768 tests)  
**Règle** : aucune modification — plan uniquement

---

## Principes d'ordonnancement

1. Les sources (`code/src/`) passent avant les tests  
2. `watchdog.py:44,59` — `except: pass` intentionnels et commentés → **NE PAS TOUCHER**  
3. ARG001 → fix = préfixer le param avec `_`  
4. `# type: ignore` interdit → fix = annotation explicite ou `importlib.import_module`  
5. Validation inter-batch : `ruff code/src/ + pytest tests/ -x -q` après le batch 1

---

## PLAN

```yaml
PLAN:

  - batch: 1
    module: "watchdog (orchestrateur support)"
    files:
      - "code/src/watchdog.py"
    errors:
      watchdog.py:
        - type: ARG001
          lines: [49]
          fix: |
            Dans la signature de _send_email_alert (no-op stub L49) :
              def _send_email_alert(subject: str, body: str) -> bool:
            Renommer en :
              def _send_email_alert(_subject: str, _body: str) -> bool:
          note: "watchdog.py:44,59 — except: pass intentionnels + commentés → NE PAS MODIFIER"
    estimated_fixes: 2
    difficulty: Facile
    priority: P2
    rationale: "Seul fichier source affecté · ARG001 sur stub no-op · risque nul"
    validation: |
      .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/watchdog.py').read()); print('OK')"
      .venv\Scripts\python.exe -m ruff check code/src/watchdog.py --select ARG
      .venv\Scripts\python.exe -m pytest tests/test_watchdog.py -x -q

  - batch: 2
    module: "tests (type_ignore)"
    files:
      - "tests/test_e2e_testnet.py"
      - "tests/test_indicators_consistency.py"
    errors:
      test_e2e_testnet.py:
        - type: type_ignore
          lines: [93, 102]
          fix: |
            Ajouter 'from typing import Any' si absent.
            L93 : def _get_btc_balance(client) -> float:  # type: ignore[no-untyped-def]
            → def _get_btc_balance(client: Any) -> float:
            L102: def _get_current_price(client) -> float:  # type: ignore[no-untyped-def]
            → def _get_current_price(client: Any) -> float:
            Supprimer les 2 commentaires # type: ignore[no-untyped-def]
      test_indicators_consistency.py:
        - type: type_ignore
          lines: [301]
          fix: |
            L301 : import backtest_engine as be  # type: ignore[import]
            → Remplacer par :
              import importlib
              be = importlib.import_module("backtest_engine")
            Supprimer le commentaire # type: ignore[import]
            (importlib.import_module retourne ModuleType → compatible Any)
    estimated_fixes: 3
    difficulty: Facile
    priority: P3
    rationale: "Tests uniquement — aucun risque capital · 3 suppressions de type:ignore"
    validation: |
      .venv\Scripts\python.exe -m pytest tests/test_e2e_testnet.py tests/test_indicators_consistency.py -x -q
      .venv\Scripts\python.exe -m pyright tests/test_e2e_testnet.py tests/test_indicators_consistency.py
```

---

## RÉSUMÉ

```yaml
RÉSUMÉ:
  total_batches    : 2
  total_files      : 3
  estimated_fixes  : 5
  repartition:
    ARG001         : 2  (watchdog.py — stub no-op)
    type_ignore    : 3  (2 test_e2e_testnet.py + 1 test_indicators_consistency.py)

  hors_scope:
    - "watchdog.py:44,59 — except: pass intentionnels (fermeture handlers logs)"
    - "benchmark.py, preload_data.py — print() intentionnels dans scripts CLI"
    - "testnet.yml — 4 warnings VS Code = faux positifs GitHub Actions extension"

  ordre_validation_final_P4: |
    .venv\Scripts\python.exe -m ruff check code/src/ --output-format concise
    .venv\Scripts\python.exe -m ruff check code/src/ --select ARG --output-format concise
    .venv\Scripts\python.exe -m pyright --project pyrightconfig.json
    .venv\Scripts\python.exe -m pytest tests/ -x -q
    → Seuil: 0 ruff · 0 ARG · 0 pyright · ≥768 passed · 0 failed

  priorite_execution:
    P2: [1]   # watchdog.py — source (priorité)
    P3: [2]   # tests only
```

---

## ORDRE D'EXÉCUTION RECOMMANDÉ POUR P3

```
Batch 1 → ruff ARG ✓ → pytest test_watchdog ✓
→ Batch 2 → pyright tests ✓ → pytest tests/ -x -q ✓
→ P4-VERIFY
```

**→ Passer à P3 · commencer par le Batch 1 :**  
`#file:tasks/audits/fix_errors/P3- FIX_core_prompt_multi_assets.md`  
`Corrige le batch 1 depuis PLAN_result.md.`

