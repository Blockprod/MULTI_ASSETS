# walk_forward.py — Contexte module

## Rôle
Sélection du meilleur scénario WF pour chaque paire via walk-forward ancré multi-split. Applique les OOS gates pour bloquer les scénarios qui ne généralisent pas en production.

## OOS Gates — seuils de validation
| Gate | Seuil minimal | Action si non-atteint |
|------|-------------|----------------------|
| `sharpe_ratio` | ≥ 0.8 | `oos_blocked = True` |
| `win_rate` | ≥ 0.30 (30%) | `oos_blocked = True` |
| `performance_decay` | ≥ 0.15 | `oos_blocked = True` |

- Toutes les conditions doivent être remplies simultanément
- `oos_blocked = True` dans `pair_state` → achats bloqués jusqu'à re-validation
- Alerte email envoyée quand `oos_blocked` change d'état (dédoublonnée via `_oos_alert_lock`)

## Walk-Forward ancré (Anchored Walk-Forward)
- **Fenêtre IS (In-Sample)** : début fixe (`_fresh_start_date()`) jusqu'au split point
- **Fenêtre OOS (Out-of-Sample)** : du split point jusqu'à aujourd'hui
- Référence méthodologique : Lopez de Prado, "Advances in Financial Machine Learning"
- Multi-split : plusieurs points de split pour réduire la variance de la mesure OOS

## Flux d'appel
```
MULTI_SYMBOLS.py (scheduler, 1x/jour ou au démarrage)
    └─► walk_forward.get_best_scenario(pair, ohlcv_df)
            └─► backtest_runner.run_backtest(scenario, df, split_date)  [×4 scénarios × N splits]
                    └─► backtest_engine_standard.pyd (Cython) ou Python fallback
            └─► Calcul métriques OOS agrégées
            └─► Vérification OOS gates
            └─► Retourne (best_scenario_name, metrics_dict, oos_blocked)
```

## `performance_decay`
- Mesure : `1 - (sharpe_OOS / sharpe_IS)` ou formule équivalente selon l'implémentation
- Un `decay` élevé (> 0.5) signale un overfitting fort → gate bloque même si Sharpe OOS est acceptable
- Un `decay` minimal de 0.15 = au plus 85% de la performance IS conservée en OOS

## État persisté
- `oos_blocked` dans `pair_state` → survit aux redémarrages
- `active_scenario` dans `pair_state` → scénario sélectionné par le dernier WF
- Re-évaluation : déclenchée périodiquement ou manuellement via la config

## À ne jamais faire
- Modifier les seuils OOS gates sans tests de backtests sur toutes les paires
- Utiliser le split IS comme validation OOS (look-ahead)
- Désactiver les OOS gates "temporairement" en production
- Ignorer `oos_blocked` lors de la génération de signaux BUY
