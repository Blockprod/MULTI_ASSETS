# Plan d'Action — Fix bloc F-BUG2 startup (`__main__`)

> creation: 2026-05-03 à 18:03
> statut: **TERMINÉ**
> priorité: P1
> source: analyse log_rsi copy.md (session 03 mai 2026)
> fichier cible: `code/src/MULTI_SYMBOLS.py` (bloc F-BUG2, ~L1667–L1730)

---

## Contexte

L'analyse du log a révélé que le bloc `__main__` de `MULTI_SYMBOLS.py` (identifié F-BUG2)
est une copie ancienne de la logique d'orchestration **qui n'a pas suivi les évolutions** de
`backtest_orchestrator._backtest_and_display_results`. Il manque trois fonctionnalités critiques
et contient un message de log trompeur.

---

## Problèmes identifiés

### P1 — STOCH-OPT absent au démarrage
- `run_stoch_threshold_grid_search()` **n'est pas appelé** dans le bloc F-BUG2.
- Conséquence : les seuils StochRSI ne sont jamais optimisés lors d'un démarrage à froid.
- Le log confirme : aucune ligne `[STOCH-OPT]` lors du démarrage analysé.

### P2 — Optuna WF absent au démarrage
- Le bloc F-BUG2 appelle uniquement `run_walk_forward_validation` (grid WF).
- `run_walk_forward_optuna` (Bayesian search, ML-07) **n'est jamais invoqué**.
- Le log confirme : aucune ligne `[ML-07]`.

### P3 — Fallback WF hardcodé (ultra-conservatif)
- Quand le WF échoue, le code impose `{1d, EMA(26/50), StochRSI}` — la config #15 IS (~$558).
- L'orchestrateur utilise correctement `_select_best_by_calmar(_pool_loop)` en fallback.
- Le `best_result = _select_best_by_calmar(_pool_loop)` est **calculé** mais **jamais utilisé**
  comme fallback dans F-BUG2.

### P4 — Message oos_blocked trompeur
- Le warning final dit : `"Achats bloqués par P0-03/oos_blocked"`.
- Or `_oos_blocked_this_pair = False` quand des configs passent les OOS gates.
- Le refus d'achat vient de `StochRSI = 100%` (overbought), pas du flag `oos_blocked`.

---

## Plan d'implémentation

### Étape 1 — Ajouter l'import `run_walk_forward_optuna` dans le bloc F-BUG2

Dans le `try` du bloc F-BUG2 (L1669), modifier l'import pour inclure
`run_walk_forward_optuna` :

```python
from walk_forward import run_walk_forward_validation as _run_wf_startup, run_walk_forward_optuna as _run_wf_optuna_startup
```

### Étape 2 — Ajouter le bloc STOCH-OPT avant l'appel WF

Après la construction de `_wf_dfs_startup` et **avant** `_run_wf_startup(...)`,
insérer le même bloc STOCH-OPT que dans `backtest_orchestrator.py` (L740-L762) :

```python
# === STOCH THRESHOLD GRID SEARCH (IS-only) ===
try:
    from backtest_orchestrator import run_stoch_threshold_grid_search as _run_stoch_opt
    _stoch_opt_startup = _run_stoch_opt(
        is_results=data['results'],
        base_dataframes=_wf_dfs_startup,
        backtest_fn=backtest_from_dataframe,
        scenario_default_params=SCENARIO_DEFAULT_PARAMS,
        sizing_mode=args.sizing_mode,
    )
    if _stoch_opt_startup:
        config.stoch_rsi_buy_min   = _stoch_opt_startup['buy_min']
        config.stoch_rsi_buy_max   = _stoch_opt_startup['buy_max']
        config.stoch_rsi_sell_exit = _stoch_opt_startup['sell_exit']
        with _bot_state_lock:
            bot_state['stoch_params'] = {
                'buy_min':   _stoch_opt_startup['buy_min'],
                'buy_max':   _stoch_opt_startup['buy_max'],
                'sell_exit': _stoch_opt_startup['sell_exit'],
            }
        save_bot_state()
        logger.info(
            "[STARTUP STOCH-OPT] Seuils optimisés: buy_min=%.2f buy_max=%.2f sell_exit=%.2f "
            "(Calmar=%.3f)",
            _stoch_opt_startup['buy_min'], _stoch_opt_startup['buy_max'],
            _stoch_opt_startup['sell_exit'], _stoch_opt_startup['avg_calmar'],
        )
except Exception as _stoch_startup_err:
    logger.warning("[STARTUP STOCH-OPT] Grid search skipped: %s", _stoch_startup_err)
```

### Étape 3 — Ajouter Optuna WF (ML-07) avec fallback grid WF

Remplacer l'appel unique `_run_wf_startup(...)` par le pattern ML-07 de
l'orchestrateur (Optuna → fallback grid si pas de config OOS valide) :

```python
# ML-07: Optuna en priorité
_wf_res_startup = _run_wf_optuna_startup(
    base_dataframes=_wf_dfs_startup,
    scenarios=WF_SCENARIOS,
    backtest_fn=backtest_from_dataframe,
    initial_capital=config.initial_wallet,
    sizing_mode=args.sizing_mode,
    n_trials=100,
)
# Fallback grid WF si Optuna ne passe pas les OOS gates
if not _wf_res_startup.get('any_passed'):
    logger.info("[STARTUP ML-07] Optuna WF: aucune config valide — fallback grid WF")
    _wf_res_startup = _run_wf_startup(
        base_dataframes=_wf_dfs_startup,
        full_sample_results=data['results'],
        scenarios=WF_SCENARIOS,
        backtest_fn=backtest_from_dataframe,
        initial_capital=config.initial_wallet,
        sizing_mode=args.sizing_mode,
    )
```

### Étape 4 — Remplacer le fallback hardcodé par best IS Calmar

Dans le bloc `else` (quand `_startup_wf_best is None`), remplacer les valeurs
hardcodées `{1d, EMA(26/50), StochRSI}` par `_select_best_by_calmar(_pool_loop)` :

**Avant :**
```python
else:
    best_params = {
        'timeframe': '1d',
        'ema1_period': 26,
        'ema2_period': 50,
        'scenario': 'StochRSI',
    }
    best_params.update(SCENARIO_DEFAULT_PARAMS.get('StochRSI', {}))
    logger.warning(
        "[STARTUP F-BUG2] Aucun WF valide — paramètres CONSERVATIFS par défaut "
        "(EMA 26/50, StochRSI, 1d). Achats bloqués par P0-03/oos_blocked."
    )
```

**Après :**
```python
else:
    _fallback_r = _select_best_by_calmar(_pool_loop) if _pool_loop else best_result
    best_params = {
        'timeframe': _fallback_r.get('timeframe', '1d'),
        'ema1_period': _fallback_r.get('ema_periods', [26, 50])[0],
        'ema2_period': _fallback_r.get('ema_periods', [26, 50])[1],
        'scenario': _fallback_r.get('scenario', 'StochRSI'),
    }
    best_params.update(SCENARIO_DEFAULT_PARAMS.get(best_params['scenario'], {}))
    logger.warning(
        "[STARTUP F-BUG2] Aucun WF valide — fallback best IS Calmar: %s EMA(%s/%s) %s.",
        best_params['scenario'],
        best_params['ema1_period'],
        best_params['ema2_period'],
        best_params['timeframe'],
    )
```

> **Note** : `_pool_loop` est la variable issue de `_apply_oos_quality_gate()` dans le bloc
> principal. Vérifier que cette variable est accessible à ce niveau de scope. Si non,
> utiliser directement `best_result` (déjà calculé juste en dessous).

### Étape 5 — Corriger le message oos_blocked trompeur

Supprimer la mention `"Achats bloqués par P0-03/oos_blocked"` du warning fallback
(traitée en Étape 4 ci-dessus — le nouveau message n'en contient pas).

---

## Validation obligatoire

```powershell
# 1. Syntaxe
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"

# 2. Tests complets
pytest tests/ -x -q
```

Résultat attendu : **≥ 797 tests passants, 0 erreur**.

---

## Risques et précautions

| Risque | Mitigation |
|--------|-----------|
| `_pool_loop` non accessible dans le scope fallback | Utiliser `best_result` en repli (déjà calculé L1703) |
| Import circulaire `backtest_orchestrator` → `MULTI_SYMBOLS` | Import local dans le `try` (déjà le pattern du projet) |
| `run_walk_forward_optuna` lente (~100 trials) au démarrage | Acceptable — même comportement que le flow scheduled/main |
| `backtest_taker_fee` modifié par accident | Ne pas toucher `backtest_runner.py` dans ce fix |

---

## Fichiers touchés

| Fichier | Modification |
|---------|-------------|
| `code/src/MULTI_SYMBOLS.py` | Bloc F-BUG2 L1667–L1730 uniquement |
| `code/src/backtest_orchestrator.py` | Aucune — `run_stoch_threshold_grid_search` déjà exporté |
| `code/src/walk_forward.py` | Aucune |

---

## Critères d'acceptation (DoD)

- [x] Log de démarrage contient une ligne `[STARTUP STOCH-OPT]`
- [x] Log de démarrage contient une ligne `[STARTUP ML-07]`
- [x] Fallback WF utilise best IS Calmar (loggé avec le nom de la config)
- [x] Aucun message `"Achats bloqués par P0-03/oos_blocked"` quand `_oos_blocked_this_pair=False`
- [x] 797+ tests passants
