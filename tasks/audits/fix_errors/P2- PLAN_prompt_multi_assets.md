---
modele: sonnet-4.6
mode: agent
contexte: codebase
produit: tasks/audits/fix_errors/fix_results/PLAN_result.md
derniere_revision: 2026-04-06
creation: 2026-04-06 à 21:10
---

#codebase

Tu es un Software Architect spécialisé systèmes de trading Python.
Tu crées un plan de correction OPTIMAL à partir du SCAN.

─────────────────────────────────────────────
INPUT
─────────────────────────────────────────────
Lire `tasks/audits/fix_errors/fix_results/SCAN_result.md` (FILES_TO_FIX).

─────────────────────────────────────────────
RAISONNEMENT
─────────────────────────────────────────────
Ne modifie rien. Raisonne sur les dépendances et groupe
les fichiers de façon à minimiser le nombre d'itérations
de vérification inter-batch.

─────────────────────────────────────────────
RÈGLES DE PRIORITÉ MULTI_ASSETS
─────────────────────────────────────────────
Batch 1 — Fondations (importés par presque tout le reste) :
  bot_config.py, constants.py, exceptions.py

Batch 2 — State & persistance :
  state_manager.py, metrics.py

Batch 3 — Exchange & réseau :
  exchange_client.py, timestamp_utils.py

Batch 4 — Orchestration core :
  order_manager.py, position_reconciler.py, backtest_orchestrator.py

Batch 5 — Données & indicateurs :
  data_fetcher.py, indicators_engine.py, indicators.py,
  signal_generator.py, market_analysis.py

Batch 6 — Backtest :
  backtest_runner.py, walk_forward.py

Batch 7 — Helpers & support :
  trade_helpers.py, trade_journal.py, position_sizing.py,
  cache_manager.py, cython_integrity.py

Batch 8 — UI & email :
  display_ui.py, email_utils.py, email_templates.py, error_handler.py

Batch 9 — Orchestrateur principal + watchdog :
  MULTI_SYMBOLS.py (3400 lignes — toujours en dernier parmi les sources)
  watchdog.py, benchmark.py, preload_data.py

Batch 10+ — Tests (groupés par module miroir) :
  test_core.py, test_state_manager.py
  → test_exchange_client.py, test_exchange_client_new.py, test_exchange_client_idempotency.py
  → test_order_manager_sl_chain.py, test_position_reconciler.py
  → test_backtest.py, test_p2_02_lookahead.py, test_p2_05_specific.py
  → test_signal_generator.py, test_indicators_check.py, test_indicators_consistency.py
  → test_trading_engine.py, test_execute_trades_unit.py, test_phase1_fixes.py
  → test_trade_helpers.py, test_trade_journal.py, test_position_sizing.py, test_sizing.py
  → test_data_fetcher.py, test_cache_manager.py
  → test_watchdog.py, test_circuit_breaker.py, test_error_handler.py
  → test_p0_fixes.py, test_p1_p2_fixes.py
  → test_backtest.py, test_walk_forward (si présent)
  → test_metrics.py, test_e2e_testnet.py (skip_by_default)
  → autres tests/*

─────────────────────────────────────────────
RÈGLES DE GROUPEMENT
─────────────────────────────────────────────
1. Max 20 fichiers par batch
2. Fichiers du même module = même batch
3. Si A importe B → B dans un batch antérieur
4. Erreurs liées au _bot_state_lock → toujours signaler en BLOCKER THREADING
5. Erreurs `cast` manquant → grouper avec les autres erreurs `typing` du même fichier
6. MULTI_SYMBOLS.py → toujours seul dans son sous-batch (taille + risque)

─────────────────────────────────────────────
CATALOGUE DE PATTERNS CONNUS MULTI_ASSETS
─────────────────────────────────────────────
(pour qualifier la difficulté de chaque batch)

| Pattern | Fix | Difficulté |
|---------|-----|-----------|
| `df[col]` → `Series \| Unknown` | `pd.Series(df[col])` | Facile |
| `Dict[str, Any]` annotation manquante | ajouter le type explicit | Facile |
| `Optional[X]` manquant | ajouter `Optional` + import | Facile |
| `pd.Timestamp \| NaTType` | `cast(pd.Timestamp, ...)` | Moyen |
| ARG002/ARG004 unused param | connecter au calcul ou préfixer `_` | Moyen |
| `except Exception: pass` muet | ajouter `logger.debug(...)` | Facile |
| `datetime.utcnow()` | → `datetime.now(timezone.utc)` | Facile |
| Accès bot_state sans `_bot_state_lock` | wrapper dans `with _bot_state_lock:` | Complexe |
| Import circulaire | déplacer dans un module tiers | Complexe |
| Signature `.pyi` Cython ≠ usage | aligner `code/bin/*.pyi` ↔ appel Python | Complexe |

─────────────────────────────────────────────
SORTIE OBLIGATOIRE
─────────────────────────────────────────────
Créer `C:\Users\averr\MULTI_ASSETS\tasks\audits\fix_errors\fix_results\PLAN_result.md` avec :

```
PLAN = [
  {
    batch: 1,
    module: "fondations",
    files: ["code/src/bot_config.py", "code/src/constants.py", "code/src/exceptions.py"],
    error_types: ["typing", "ARG"],
    estimated_fixes: N,
    difficulty: Facile | Moyen | Complexe
  },
  ...
]

RÉSUMÉ:
  total_batches    : X
  total_files      : Y
  estimated_fixes  : Z
  ordre_validation : pytest tests/ -x -q → ruff code/src/ → pyright --project pyrightconfig.json
```

SORTIE OBLIGATOIRE :
Tous les résultats doivent être enregistrés dans :
C:\Users\averr\MULTI_ASSETS\tasks\audits\fix_errors\fix_results

─────────────────────────────────────────────
CONTRAINTES ABSOLUES
─────────────────────────────────────────────
- Aucune modification de code
- Si FILES_TO_FIX est vide → écrire "PLAN : rien à corriger ✅"
- Confirmer dans le chat : "✅ PLAN_result.md créé · X batches · Y fichiers"
