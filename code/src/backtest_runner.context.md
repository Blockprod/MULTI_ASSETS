# backtest_runner.py — Contexte module

## Rôle
Exécution des backtests pour les `WF_SCENARIOS`. Produit les métriques OOS (Sharpe, WinRate, Calmar, decay) utilisées par `walk_forward.py` pour sélectionner le scénario actif.

## WF_SCENARIOS (4 scénarios, définis dans `MULTI_SYMBOLS.py`)
| Scénario | Indicateurs actifs |
|----------|-------------------|
| `StochRSI` | StochRSI uniquement |
| `StochRSI_SMA200` | StochRSI + filtre SMA200 |
| `StochRSI_SMA200_ADX` | StochRSI + SMA200 + ADX |
| `StochRSI_SMA200_ADX_TRIX` | StochRSI + SMA200 + ADX + TRIX |

## Fees backtest — RÈGLE CRITIQUE
- `backtest_taker_fee = 0.0007` et `backtest_maker_fee = 0.0002` sont **figés**
- Ces valeurs NE DOIVENT **JAMAIS** être synchronisées avec les fees live (`taker_fee`, `maker_fee`)
- Toute modification de ces constantes doit être faite manuellement et documentée dans ce fichier

## Fenêtre de backtest
- Calculée dynamiquement via `_fresh_start_date()` : `today - 1095 jours`
- **Ne jamais** utiliser une date figée à l'import (ex: `START_DATE = "2022-01-01"`)
- La fenêtre glissante garantit que le backtest reste pertinent dans le temps

## Filtre MTF 4h (anti look-ahead)
- EMA18 et EMA58 calculées sur les bougies 4h
- **Obligatoire** : `shift(1)` sur les bougies 4h avant alignement avec les bougies 1h
- `shift(1)` = on utilise la bougie 4h **précédente** (clôturée), pas la courante
- Tout `shift(0)` ou absence de `shift` = look-ahead bias → backtest invalide

## Moteur de calcul
- **Premier choix** : `backtest_engine_standard.pyd` (Cython compilé, ×10-50 plus rapide)
- **Fallback** : implémentation Python pure si `.pyd` absent
- Import conditionnel : `try: from code.bin import backtest_engine_standard except ImportError: ...`

## Métriques produites par scénario
| Métrique | Description | Gate OOS |
|---------|-------------|---------|
| `sharpe_ratio` | Sharpe annualisé OOS | ≥ 0.8 |
| `win_rate` | % trades gagnants OOS | ≥ 30% |
| `performance_decay` | Dégradation IS → OOS | ≥ 0.15 |
| `calmar_ratio` | Rendement / MaxDrawdown | info seulement |
| `total_return_pct` | Rendement total OOS | info seulement |

## Benchmarks validés (ne pas s'en éloigner sans analyse)
- Calmar IS : ~2.004 (référence)
- Sharpe OOS : 0.6–0.9 selon la paire et le régime
- WinRate : 35–55% selon le scénario

## À ne jamais faire
- Utiliser `config.taker_fee` ou `config.maker_fee` dans les calculs de backtest
- Modifier la fenêtre de 1095 jours sans valider l'impact sur toutes les paires
- Supprimer le `shift(1)` MTF "pour simplifier"
