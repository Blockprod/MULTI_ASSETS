# PLAN_result — MULTI_ASSETS

**Date** : 2026-04-06 21:14  
**Source** : `SCAN_result.md` (36 violations · 17 fichiers · 0 erreur pyright)  
**Règle** : aucune modification — plan uniquement

---

## Principes d'ordonnancement

1. Les fichiers importés par d'autres passent en premier (bot_config, constants → tous sains → skip)  
2. `MULTI_SYMBOLS.py` (3400 lignes) toujours seul dans son sous-batch  
3. `watchdog.py:44,59` — `except: pass` intentionnels et commentés → **NE PAS TOUCHER**  
4. `except Exception: pass` muet → fix = `logger.debug("...", exc_info=True)` ou `logger.warning`  
5. ARG001 → fix = préfixer le param avec `_` si non utilisable, sinon connecter au calcul  
6. `# type: ignore[name-defined]` dans `walk_forward.py` → fix = `TYPE_CHECKING` guard + `cast`  
7. Validation inter-batch : `ruff + pytest -x -q` après chaque batch

---

## PLAN

```yaml
PLAN:

  - batch: 1
    module: "state & persistance"
    files:
      - "code/src/state_manager.py"
      - "code/src/metrics.py"
    errors:
      state_manager.py:
        - type: silent_except
          lines: [181]
          fix: "logger.debug('state_manager exception', exc_info=True)"
      metrics.py:
        - type: silent_except
          lines: [113]
          fix: "logger.debug('metrics write exception', exc_info=True)"
    estimated_fixes: 2
    difficulty: Facile
    rationale: "Modules bas niveau · peu de dépendances · valider avant exchange"

  - batch: 2
    module: "exchange & réseau"
    files:
      - "code/src/exchange_client.py"
      - "code/src/timestamp_utils.py"
    errors:
      exchange_client.py:
        - type: ARG001
          lines: [708]
          fix: "préfixer 'limit_slippage' → '_limit_slippage'"
        - type: silent_except
          lines: [422]
          fix: "logger.debug('exchange_client exception', exc_info=True)"
        - type: print_prod
          lines: [262, 268]
          fix: "remplacer print() dans boucle retry → logger.debug('[RETRY]...', ...)"
      timestamp_utils.py:
        - type: silent_except
          lines: [273]
          fix: "logger.debug('timestamp_utils exception', exc_info=True)"
    estimated_fixes: 5
    difficulty: Moyen
    rationale: "Critique — importe dans tout le bot · print() dans boucle retry"

  - batch: 3
    module: "orchestration core"
    files:
      - "code/src/order_manager.py"
      - "code/src/position_reconciler.py"
      - "code/src/backtest_orchestrator.py"
    errors:
      order_manager.py:
        - type: ruff_F401
          lines: [34]
          fix: "supprimer import 'DUST_FINAL_FRACTION as _DUST_FINAL_FRACTION' si inutilisé partout dans le fichier"
      position_reconciler.py:
        - type: silent_except
          lines: [99]
          fix: "logger.debug('position_reconciler exception', exc_info=True)"
      backtest_orchestrator.py:
        - type: ARG001
          lines: [461, 462]
          fix: "préfixer 'start_date' → '_start_date', 'timeframes' → '_timeframes'"
    estimated_fixes: 4
    difficulty: Facile
    rationale: "Vérifier que _DUST_FINAL_FRACTION n'est pas utilisé plus bas dans order_manager avant suppression"

  - batch: 4
    module: "données & indicateurs"
    files:
      - "code/src/data_fetcher.py"
      - "code/src/indicators_engine.py"
    errors:
      data_fetcher.py:
        - type: silent_except
          lines: [251, 300]
          fix: "logger.debug('data_fetcher exception', exc_info=True)"
      indicators_engine.py:
        - type: silent_except
          lines: [277, 375]
          fix: "logger.debug('indicators_engine exception', exc_info=True)"
    estimated_fixes: 4
    difficulty: Facile
    rationale: "Modules données — sans écriture sur bot_state, faible risque"

  - batch: 5
    module: "backtest"
    files:
      - "code/src/backtest_runner.py"
      - "code/src/walk_forward.py"
    errors:
      backtest_runner.py:
        - type: silent_except
          lines: [406]
          fix: "logger.debug('backtest_runner exception', exc_info=True)"
      walk_forward.py:
        - type: type_ignore
          lines: [723]
          fix: |
            Remplacer:
              def _objective(trial: 'optuna.Trial') -> float:  # type: ignore[name-defined]
            Par:
              from typing import TYPE_CHECKING
              if TYPE_CHECKING:
                  import optuna
              def _objective(trial: 'optuna.Trial') -> float:
            (ou utiliser Any si optuna non installé en prod)
    estimated_fixes: 2
    difficulty: Moyen
    rationale: "walk_forward.py:723 — type:ignore interdit. Valider avec pytest tests/test_backtest.py après"

  - batch: 6
    module: "cache"
    files:
      - "code/src/cache_manager.py"
    errors:
      cache_manager.py:
        - type: silent_except
          lines: [89, 97, 107, 145, 148, 181, 187, 266, 276]
          fix: "logger.debug('cache_manager exception', exc_info=True) — 9 occurrences"
    estimated_fixes: 9
    difficulty: Facile
    rationale: "Volumineux mais mécanique · un seul fichier · toutes les corrections identiques"

  - batch: 7
    module: "orchestrateur principal"
    files:
      - "code/src/MULTI_SYMBOLS.py"
    errors:
      MULTI_SYMBOLS.py:
        - type: ruff_F401
          lines: [89]
          fix: "supprimer l'import inutilisé 'email_utils.send_email_alert_with_fallback'"
        - type: ruff_F401
          lines: [156]
          fix: "supprimer l'import inutilisé 'cython_integrity.CYTHON_INTEGRITY_VERIFIED'"
        - type: ARG001
          lines: [1632]
          fix: "préfixer l'argument de signal handler → '_frame'"
        - type: silent_except
          lines: [165]
          fix: "logger.debug('MULTI_SYMBOLS import exception', exc_info=True)"
    estimated_fixes: 4
    difficulty: Moyen
    rationale: "RÈGLE : toujours seul dans son batch · 3400 lignes · tester IMMÉDIATEMENT après"
    validation_obligatoire: "pytest tests/ -x -q après ce batch"

  - batch: 8
    module: "watchdog"
    files:
      - "code/src/watchdog.py"
    errors:
      watchdog.py:
        - type: ARG001
          lines: [49, 49]
          fix: "préfixer 'subject' → '_subject', 'body' → '_body' dans la signature du handler"
        - type: HORS_SCOPE
          lines: [44, 59]
          note: "except: pass intentionnels + commentés → NE PAS MODIFIER"
    estimated_fixes: 2
    difficulty: Facile
    rationale: "Petit fichier · ARG001 sur un handler de signal uniquement"

  - batch: 9
    module: "tests"
    files:
      - "tests/test_watchdog.py"
      - "tests/test_p0_fixes.py"
      - "tests/test_phase1_fixes.py"
    errors:
      test_watchdog.py:
        - type: ruff_F401
          lines: [415, 430]
          fix: "supprimer imports inutilisés MagicMock + shutil"
      test_p0_fixes.py:
        - type: ruff_F401
          lines: [559]
          fix: "supprimer import inutilisé 'importlib'"
      test_phase1_fixes.py:
        - type: ruff_F401
          lines: [471]
          fix: "supprimer import inutilisé 'bot_config'"
    estimated_fixes: 4
    difficulty: Facile
    rationale: "Tests uniquement — aucun risque capital · vérifier avec pytest tests/ -x -q après"
```

---

## RÉSUMÉ

```yaml
RÉSUMÉ:
  total_batches    : 9
  total_files      : 17
  estimated_fixes  : 36
  repartition:
    silent_except  : 22  (majoritaire — 62%)
    ruff_F401      : 7
    ARG001         : 7
    type_ignore    : 1   (walk_forward.py — seul cas complexe)
    print_prod     : 2   (exchange_client.py)

  hors_scope:
    - "watchdog.py:44,59 — except: pass intentionnel (fermeture handlers logs)"
    - "code/scripts/p003_functions.py — fichier legacy (118 erreurs IDE)"
    - "benchmark.py, preload_data.py — print() intentionnels dans scripts utilitaires"

  ordre_validation_inter_batch: |
    Après chaque batch :
    1. .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/<fichier>.py').read()); print('OK')"
    2. .venv\Scripts\python.exe -m ruff check code/src/ --output-format concise
    3. .venv\Scripts\python.exe -m pytest tests/ -x -q

  ordre_validation_final_P4: |
    .venv\Scripts\python.exe -m ruff check code/src/ --output-format concise
    .venv\Scripts\python.exe -m ruff check code/src/ --select ARG --output-format concise
    .venv\Scripts\python.exe -m pyright --project pyrightconfig.json
    .venv\Scripts\python.exe -m pytest tests/ -x -q
    → Seuil: 0 ruff · 0 ARG · 0 pyright · ≥739 passed · 0 failed

  priorite_execution:
    urgent: [1, 2]       # state_manager + exchange_client → impact runtime immédiat
    important: [3, 4, 5, 6]
    routine: [7, 8, 9]
```

---

## ORDRE D'EXÉCUTION RECOMMANDÉ POUR P3

```
Batch 1 → pytest ✓ → Batch 2 → pytest ✓ → Batch 3 → pytest ✓
→ Batch 4 → pytest ✓ → Batch 5 → pytest ✓ → Batch 6 → pytest ✓
→ Batch 7 → pytest OBLIGATOIRE → Batch 8 → Batch 9 → pytest final ✓
→ ruff complet → P4-VERIFY
```

**→ Passer à P3 · commencer par le Batch 1 :**  
`Corrige le batch 1 depuis PLAN_result.md.`
