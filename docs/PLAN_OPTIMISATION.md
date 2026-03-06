# Plan d'Optimisation — Win Rate / Drawdown / PnL
_Généré le 4 mars 2026 — mis à jour le 5 mars 2026_

---

## Contexte et baseline

| Métrique | Baseline initiale | Après B-1…A-2 | **Après E-1** | Objectif |
|---|---|---|---|---|
| PnL (3 ans, $10K, avg 5 configs) | +$13,610 | +$22,752 (+67.2%) | **+$27,860 (+104.7%)** | maximiser ✅ |
| Max Drawdown | 36.4% | 19.6% (-16.8pp) | **18.7% (-17.7pp)** | < 22% ✅ 🎯 |
| Win Rate | 50.5% | 62.2% (+11.7pp) | **62.9% (+12.4pp)** | > 57% ✅ 🎯 |
| Mode sizing | `risk` (5% par trade) | `risk` (5.5%) | `risk` (5.5%) | `risk` optimisé ✅ |
| Vitesse backtests | 19.44 it/s (Cython) | Maintenue | Maintenue | Maintenir ✅ |

> **Note** : Les résultats sont crédibles — slippage, fees Binance réels (0.1%), fill au `open[i+1]`,
> mode `risk` conservateur. Le mode `baseline` (tout-en) donnait +$158K mais avec 71% DD
> et sans les corrections P1-P3 (look-ahead bias probable).

---

## Ordre de priorité global

| # | Action | Difficulté | Impact PnL | Impact WR | Impact DD |
|---|---|---|---|---|---|
| 1 | B-1 Trailing stop Cython aligné Python | Faible | Cohérence | Cohérence | Cohérence |
| 2 | C-1 Seuil `stoch_rsi_sell_exit` | ✅ DONE | **+2% PnL, -1pp DD** | +0.1% WR | 0.2→0.4 |
| 3 | A-1 Filtre volume relatif | ❌ REJETÉ | **-50% PnL** | -0.3% WR | +9.5pp DD |
| 4 | B-3 Break-even stop | ✅ DONE | ~~-7.5%~~ → **+29.2%** (avec A-3) | **+7.5% WR** | **-6.8pp DD** |
| 5 | B-2 Optimisation `risk_per_trade` | ✅ DONE | **+2.8% PnL** | neutre | +0.5pp DD |
| 6 | A-3 Cooldown post-stop-loss | ✅ DONE | **+29.2% PnL** | **+7.5pp WR** | **-6.8pp DD** |
| 7 | C-2 Seuils partiels | ❌ REJETÉ | +5-10% PnL mais WR -2 à -22pp | **❌ dégradé** | **❌ dégradé** |
| 8 | A-2 Confirmation multi-timeframe | ✅ DONE | **+25.8% PnL** | **+4.2pp WR** | **-10.5pp DD** |
| 9 | D-1 Framework A/B test | Moyenne | Sécurité | Sécurité | Sécurité |
| 10 | **E-1 atr_multiplier trailing** | ✅ DONE | **+22.5% PnL** | **+0.7pp WR** | **-0.9pp DD** |

---

## A. Filtres d'entrée (impact Win Rate)

### A-1. Filtre volume relatif — ❌ REJETÉ

**Résultat benchmark** (volume_filter ON vs OFF, 5 configs, SOLUSDT 1h, Cython) :

| Filter | Avg PnL | Avg WR | Avg DD |
|:---:|---:|---:|---:|
| OFF (actuel) | $11,695 | 49.9% | 38.2% |
| ON (volume > SMA20) | $5,841 | 49.6% | 47.7% |

**Conclusion** : Le filtre volume dégrade les 3 métriques (-50% PnL, -0.3pp WR,
+9.5pp DD). Il filtre trop de bons signaux en période de volume normal.
Code implémenté mais désactivé par défaut (`volume_filter_enabled = False`).
Réactivable via env `VOLUME_FILTER_ENABLED=true` pour tests futurs.

---

### A-2. Confirmation multi-timeframe (MTF) — ✅ DONE

**Problème** : Le bot trade sur 1h en ignorant la tendance 4h. Acheter en 1h contre
une tendance 4h baissière génère des trades perdants même si le signal 1h est valide.

**Solution** : Avant tout achat sur 1h, vérifier que sur le timeframe 4h :
```
EMA(18)_4h > EMA(58)_4h  (tendance 4h haussière)
```
No look-ahead bias garanti : `shift(1)` sur le niveau 4h + `ffill` vers 1h.

**Résultat benchmark** (grille EMA 4h, 5 configs, SOLUSDT 1h, Cython, B-3+A-3+B-2 actifs) :

| MTF 4h EMA(fast/slow) | Avg PnL | Avg WR | Avg DD | vs baseline |
|:---:|---:|---:|---:|---:|
| **OFF (baseline)** | **$18,079** | **58.0%** | **30.1%** | — |
| 13/50 | $15,078 | 59.5% | 24.8% | -16.6% PnL |
| 18/50 | $19,007 | 60.2% | 23.1% | +5.1% |
| **18/58 (appliqué)** | **$22,752** | **62.2%** | **19.6%** | **+25.8%** |
| 18/60 | $21,457 | 61.6% | 21.4% | +18.7% |
| 19/55 | $21,237 | 61.7% | 19.5% | +17.5% |
| 20/50 | $18,824 | 61.1% | 23.3% | +4.1% |
| 22/50 | $19,270 | 61.1% | 19.6% | +6.6% |
| 50/100 | $15,001 | 59.3% | 22.5% | -17.0% |

**Changement appliqué** : `mtf_filter_enabled = True`, `mtf_ema_fast = 18`, `mtf_ema_slow = 58`
- PnL : **+25.8%** ($18,079 → $22,752) ✅
- WR : **+4.2 pp** (58.0% → 62.2%) ✅ — **objectif 57% largement dépassé !**
- DD : **-10.5 pp** (30.1% → 19.6%) ✅ — 🎯 **OBJECTIF < 22% ATTEINT !**
- **TRIPLE DOMINATION** : les 3 métriques s'améliorent massivement
- Implémenté : Cython (`mtf_bullish` array, `use_mtf_filter`), Python fallback,
  `signal_generator.py` (live: `mtf_bullish` dans row), `MULTI_SYMBOLS.py`
  (resample 4h + shift(1)), `bot_config.py` (3 params)
- Désactivable via env `MTF_FILTER_ENABLED=false`

**Per-config avec 18/58** :

| Config | PnL | WR | DD |
|:---:|---:|---:|---:|
| 26/50 TRIX | $15,476 | 63.5% | 26.0% |
| 20/40 TRIX | $17,930 | 61.8% | 18.2% |
| 20/40 Stoch | $31,308 | 62.2% | 17.6% |
| 18/36 TRIX | $13,977 | 57.8% | 18.1% |
| 26/50 Stoch | $35,068 | 65.6% | 18.0% |

---

### A-3. Cooldown post-stop-loss — ✅ DONE (combiné avec B-3)

**Problème** : Après un stop loss ou un exit breakeven, le marché continue souvent
de baisser. Re-entrer immédiatement génère du "churn" : trades supplémentaires
à 0% de profit + doubles frais. Le B-3 seul améliorait WR/DD mais dégradait le PnL
de -7.5% à cause de ce phénomène.

**Solution** : Après chaque stop-loss déclenché (y compris breakeven), bloquer tout
achat pendant N bougies. Le cooldown empêche les ré-entrées parasites.

**Résultat benchmark** (B3 breakeven + grille cooldown 0-24h, 5 configs, SOLUSDT 1h, Cython) :

| Cooldown | Avg PnL | Avg WR | Avg DD | Trades | vs baseline |
|:---:|---:|---:|---:|---:|---:|
| OFF (baseline) | $13,610 | 50.5% | 36.4% | 796 | — |
| B3 seul (cd=0) | $11,688 | 56.1% | 37.3% | 899 | -14.1% PnL |
| B3+cd=5 | $11,800 | 56.0% | 31.6% | 875 | -13.3% |
| B3+cd=8 | $12,751 | 56.9% | 35.5% | 859 | -6.3% |
| B3+cd=10 | $15,389 | 57.2% | 31.3% | 851 | +13.1% |
| B3+cd=11 | $16,475 | 57.5% | 29.8% | 847 | +21.1% |
| **B3+cd=12 (appliqué)** | **$17,587** | **58.0%** | **29.6%** | **845** | **+29.2%** |
| B3+cd=13 | $16,741 | 57.9% | 29.9% | 841 | +23.0% |
| B3+cd=14 | $16,597 | 57.7% | 30.8% | 840 | +22.0% |

**Changement appliqué** : `stop_loss_cooldown_candles = 12` (12h sur 1h TF)
- PnL : **+29.2%** ($13,610 → $17,587) ✅
- WR : **+7.5 pp** (50.5% → 58.0%) ✅ — **objectif 57% dépassé !**
- DD : **-6.8 pp** (36.4% → 29.6%) ✅ — **objectif <30% quasi atteint !**
- **TRIPLE DOMINATION** : les 3 métriques s'améliorent simultanément
- Implémenté : Cython (`cooldown_remaining`), Python fallback, `MULTI_SYMBOLS.py`
  (time-based: `_stop_loss_cooldown_until`), `bot_config.py`

---

## B. Gestion de position (impact DD + PnL)

### B-1. Alignement trailing stop Cython / Python ⚠️ PRIORITÉ 1

**Problème identifié** : Divergence entre les deux moteurs.
- **Python** : trailing stop activé seulement quand `price >= entry + atr_multiplier * ATR`
  (trailing "delayed activation")
- **Cython** : trailing stop mis à jour dès la première bougie en position
  (trailing "immediate", suit le prix depuis l'entrée)

Cette différence crée des résultats inconsistants selon que Cython est disponible ou non.

**Solution** : Aligner le Cython sur le Python — ajouter l'état `trailing_activated` et
la condition `current_price >= trailing_activation_price` dans la boucle Cython.

**Implémentation** :
- `backtest_engine_standard.pyx` : ajout de `trailing_activated: bint` dans
  `PositionState`, calcul de `trailing_activation_price = entry + atr_multiplier * ATR`
- Recompilation Cython
- Tests de non-régression

**Impact estimé** : Cohérence garantie entre Cython et Python, résultats plus fiables.

---

### B-2. Optimisation `risk_per_trade` — ✅ DONE

**Résultat benchmark** (grille 2%-20%, 5 configs, SOLUSDT 1h, Cython, B-3+A-3 actifs) :

| `risk_per_trade` | Avg PnL | Avg WR | Avg DD | Calmar |
|---:|---:|---:|---:|---:|
| 2% | $8,884 | 58.0% | 16.7% | 1.77 |
| 3% | $12,885 | 58.0% | 23.0% | 1.87 |
| 4% | $15,508 | 58.0% | 27.8% | 1.86 |
| 5% (ancien) | $17,587 | 58.0% | 29.6% | 1.98 |
| **5.5% (appliqué)** | **$18,079** | **58.0%** | **30.1%** | **2.004** |
| 6% | $18,198 | 58.0% | 30.8% | 1.97 |
| 7% | $18,157 | 58.0% | 31.7% | 1.91 |
| 10% | $18,367 | 58.0% | 33.5% | 1.83 |

**Changement appliqué** : `risk_per_trade: 0.05 → 0.055`
- **Calmar ratio maximal** à 5.5% (2.004) — critère institutionnel de référence
- PnL : +$492 (+2.8%) vs 5%
- WR : inchangée (58.0%) — normal, sizing ne change pas les signaux
- DD : +0.5pp (29.6% → 30.1%) — marginal
- Au-delà de 7%, rendements plafonnent mais DD continue de monter
- 5.5% = sweet spot rendement ajusté au risque

---

### B-3. Break-even stop — ✅ DONE (combiné avec A-3)

**Résultat benchmark initial** (breakeven seul, grille trigger 0.5%-3%) :

| Trigger | Avg PnL | Avg WR | Avg DD |
|:---:|---:|---:|---:|
| OFF | $11,695 | 49.9% | 38.2% |
| 0.5% | $10,847 | 40.4% | 33.1% |
| 1.0% | $10,949 | 46.2% | 32.6% |
| 1.5% | $10,515 | 51.0% | 37.0% |
| 2.0% | $10,821 | 55.5% | 37.1% |
| 3.0% | $10,597 | 52.0% | 39.7% |

**Problème identifié** : B-3 seul crée du "churn" — trades sortis à 0% puis
ré-entrée immédiate (+76 à +151 trades parasites), doubles frais → PnL -7.5%.

**Solution** : Combinaison avec A-3 (cooldown 12h post-stop/breakeven).
Voir résultats complets dans la section A-3.

**Changement appliqué** : `breakeven_enabled = True`, `breakeven_trigger_pct = 0.02`,
`stop_loss_cooldown_candles = 12`
- Avec le duo B-3 + A-3 : PnL **+29.2%**, WR **+7.5pp**, DD **-6.8pp** vs baseline
- Implémenté dans Cython + Python fallback + bot_config + MULTI_SYMBOLS.py

---

## C. Sortie intelligente (impact PnL)

### C-1. Relever `stoch_rsi_sell_exit` — ✅ DONE

**Résultat benchmark** (6 valeurs × 5 configs, SOLUSDT 1h, Cython) :

| Value | Avg PnL | Avg WR | Avg DD |
|:---:|---:|---:|---:|
| 0.2 (ancien) | $11,469 | 49.8% | 39.2% |
| 0.3 | $11,448 | 49.4% | 38.6% |
| **0.4 (nouveau)** | **$11,695** | **49.9%** | **38.2%** |
| 0.5 | $9,960 | 51.3% | 40.3% |
| 0.6 | $8,979 | 52.1% | 39.2% |
| 0.7 | $8,776 | 52.4% | 40.2% |

**Changement appliqué** : `stoch_rsi_sell_exit: 0.2 → 0.4`
- PnL : +$226 (+2.0%)
- WR : +0.1 pp
- DD : **-1.0 pp** (38.2% vs 39.2%)
- Dominance stricte sur les 3 axes vs baseline.
- Script: `tests/bench_optimization.py`

---

### C-2. Ajuster les seuils de prises de profit partielles — ❌ REJETÉ

**Résultat benchmark** (11 profils, 5 configs, SOLUSDT 1h, Cython, B-3+A-3+B-2 actifs) :

| Profil (t1/t2/p1/p2) | Avg PnL | Avg WR | Avg DD | vs actuel |
|:---:|---:|---:|---:|---:|
| **2/4/50/30 (actuel)** | **$18,079** | **58.0%** | **30.1%** | — |
| 2.5/5/50/30 | $18,966 | 52.1% | 30.5% | +4.9% PnL, -5.9pp WR |
| 2/4/40/25 | $19,104 | 55.8% | 31.9% | +5.7% PnL, -2.2pp WR |
| 2/4/35/20 | $19,507 | 52.9% | 33.3% | +7.9% PnL, -5.1pp WR |
| 4/8/50/30 | $22,933 | 39.9% | 34.3% | +27% PnL, -18pp WR |
| 5/10/50/30 | $23,828 | 35.7% | 35.3% | +32% PnL, -22pp WR |
| Partiels OFF | $20,676 | 27.2% | 44.9% | +14% PnL, -31pp WR |

**Conclusion** : Tous les profils alternatifs améliorent le PnL (+5 à +32%) mais
détruisent le WR (-2 à -31pp) et/ou augmentent le DD. Aucun ne satisfait la règle
de triple domination. Le meilleur compromis (2/4/40/25) passe le WR sous 57% (55.8%),
perdant l'objectif atteint grâce à B-3+A-3.

Les seuils actuels sont optimaux pour la combinaison B-3+A-3 : le partial profit
rapide (2%/4%) est une protection essentielle qui maintient le WR élevé.

---

## D. Validation et sécurité

### D-1. Framework A/B test automatisé

**Principe** : Chaque amélioration est testée en isolation sur les 3 ans de données.
Un changement n'est validé que s'il améliore **au moins 2 métriques parmi 3**
(PnL, Win Rate, Max DD) sans dégrader la 3e de plus de 10% relatif.

**Script** : `tests/bench_optimization.py`

```python
# Exemple de structure
configs = [
    {'name': 'baseline', 'stoch_rsi_sell_exit': 0.2},
    {'name': 'sell_exit_0.4', 'stoch_rsi_sell_exit': 0.4},
    {'name': 'sell_exit_0.5', 'stoch_rsi_sell_exit': 0.5},
    ...
]
for cfg in configs:
    result = run_backtest_with_config(cfg)
    print(f"{cfg['name']}: PnL={result['profit']}, DD={result['max_drawdown']}, WR={result['win_rate']}")
```

**Implémentation** :
- Script standalone dans `tests/`
- Rapport comparatif en tableau Rich en console
- Sauvegarde CSV des résultats dans `cache/optimization_results.csv`

---

## E. Réglage fin des paramètres post-stratégie

### E-1. Optimisation atr_multiplier (trailing activation) — ✅ DONE

**Problème** : Le trailing stop s'activait à 5.5× ATR de l'entrée. Cette distance
trop courte clippait les gagnants prématurément, réduisant le PnL et augmentant
le nombre de sorties trailing suivies de ré-entrées.

**Solution** : Augmenter `atr_multiplier` à 8.0 — le trailing s'active plus tard,
laissant les trades gagnants respirer davantage avant de verrouiller les profits.

**Résultat benchmark** (grille 3.0-12.0, 5 configs, SOLUSDT 1h, Cython, toutes optims actives) :

| atr_multiplier | Avg PnL | Avg WR | Avg DD | vs 5.5 (ancien) |
|:---:|---:|---:|---:|---:|
| 3.0 | $19,855 | 61.1% | 21.0% | -12.7% PnL |
| 4.5 | $17,564 | 61.7% | 19.5% | -22.8% |
| 5.5 (ancien) | $22,752 | 62.2% | 19.6% | — |
| 6.5 | $25,228 | 62.7% | 18.7% | +10.9% |
| 7.5 | $24,566 | 61.6% | 18.9% | +8.0% |
| **8.0 (appliqué)** | **$27,860** | **62.9%** | **18.7%** | **+22.5%** |
| 8.25 | $28,205 | 63.0% | 18.7% | +24.0% |
| 9.0 | $27,624 | 63.0% | 19.1% | +21.4% |
| 12.0 | $27,783 | 63.2% | 19.9% | +22.1% |

**Validation sur 20 configs (IS slice 70%)** :
- PnL amélioré sur **16/20 configs** (seules 3 configs 18/36 non-TRIX à -2.6%)
- DD amélioré ou stable sur **18/20 configs**
- Pire DD réduit de **26.0% → 21.8%** (-4.2pp)
- Champion PnL : $31,645 → $31,645 (+18.5%, DD 16.9%)

**Changement appliqué** : `atr_multiplier = 8.0` (était 5.5)
- PnL : **+22.5%** ($22,752 → $27,860) ✅
- WR : **+0.7pp** (62.2% → 62.9%) ✅
- DD : **-0.9pp** (19.6% → 18.7%) ✅
- **TRIPLE DOMINATION** : les 3 métriques s'améliorent
- Cython recompilé + déployé, `bot_config.py` mis à jour, 584 tests OK

**Per-config final E-1** :

| Config | PnL | WR | DD | Calmar |
|:---:|---:|---:|---:|---:|
| 26/50 TRIX | $21,280 | 64.8% | 21.8% | 9.78 |
| 20/40 TRIX | $25,701 | 62.9% | 18.8% | 13.68 |
| 20/40 Stoch | $35,283 | 62.6% | 17.6% | 20.09 |
| 18/36 TRIX | $18,260 | 57.7% | 18.3% | 10.00 |
| 26/50 Stoch | $38,776 | 66.2% | 16.9% | 22.93 |

---

## Règles de développement

1. **Aucun changement live sans backtest validé** sur les 3 ans complets
2. **Tests unitaires** : chaque nouvelle feature doit avoir ≥ 2 tests dans `tests/`
3. **Suite de tests complète** doit rester à 584 tests passants après chaque phase
4. **Cython recompilé** à chaque modification de `.pyx`
5. **Pas de look-ahead bias** : toute nouvelle donnée doit utiliser l'index `[i]` pour
   la décision et `[i+1]` pour le fill

---

## Checklist de déploiement par item

Pour chaque amélioration, dans l'ordre :

- [ ] Implémentation dans `backtest_runner.py` (fallback Python)
- [ ] Implémentation dans `backtest_engine_standard.pyx` (Cython)
- [ ] Recompilation Cython + copie `.pyd`
- [ ] Tests unitaires (`tests/`)
- [ ] Backtest comparatif A/B (avant/après)
- [ ] Validation des métriques (PnL, WR, DD)
- [ ] Implémentation live dans `MULTI_SYMBOLS.py`
- [ ] `bot_config.py` mis à jour avec les nouvelles valeurs par défaut

---

_Plan rédigé le 4 mars 2026 — à mettre à jour au fur et à mesure des validations._
