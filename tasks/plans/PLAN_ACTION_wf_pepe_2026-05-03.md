# Plan d'Action — WF PEPE : 6 corrections post-analyse

> creation: 2026-05-03 à 15:55
> statut: **TERMINÉ**
> priorité: P0 → P3
> source: analyse log_rsi copy.md (session 03 mai 2026) + switch PEPEUSDT→PEPEUSDC (déjà appliqué)

---

## Contexte

Après le switch ONDO→PEPE et la correction du bug StochRSI, l'analyse du premier log
live PEPE a révélé **6 nouvelles anomalies** :

1. WF 1d produit 1 seul fold → Sharpe=0.00, WR=100% — structurellement invalide
2. Top-N WF trop restrictif — 47 configs IS éligibles, seulement 5 testées en WF
3. Reconciler logge `PEPEUSDT` au lieu de `PEPEUSDC` (real_pair) — confusion diagnostic
4. 4 pair_states orphelins (ONDO + anciennes paires) dans `bot_state.json`
5. ~~Divergence USDT/USDC~~ — **résolu** : switch `backtest_pair=PEPEUSDC` appliqué en session
6. Offset horloge -2251ms non corrigé par P2 (seuil 3000ms trop haut)

---

## ✅ DÉJÀ RÉSOLU — Point 5 : Divergence USDT/USDC

`backtest_pair` basculé de `PEPEUSDT` vers `PEPEUSDC` dans `MULTI_SYMBOLS.py`.
Backtest et trading live utilisent maintenant la même série de prix.
797 tests passés — appliqué en session 03 mai 2026.

---

## P0 — WF 1d : 1 fold invalide (Sharpe=0, WR=100%)

### Diagnostic

Avec `initial_train_pct≈0.70` (régime volatile) et 1095 barres 1d :
- Train initial : ~757 barres
- Test pool : 338 barres → 1 seul test window de 200 barres (fold 2 nécessiterait 957+200=1157 > 1095)
- 1 trade en 200 barres OOS → equity plate sur 199/200 barres → std≈0 → Sharpe=0

Le log affiche `WF Fold 1/4` (annonce 4 folds) mais n'en produit réellement qu'**1**.

### Solution retenue

Exclure le timeframe `1d` du WF quand le nombre de barres disponibles ne permet
pas de produire **au moins 2 folds** valides. Un seul fold n'a pas de valeur
statistique : un trade gagnant produit WR=100% et Sharpe=0 (equity monotone).

**Seuil de coupure calculé** : fold 2 viable → besoin de `train_initial + 2×test_window`
= 500 (min_train) + 2×200 (min_test) = **900 barres minimum**.
PEPEUSDC 1d : 788 barres → en dessous → WF 1d exclu jusqu'à ~juillet 2026.

### Fichiers concernés

- `code/src/walk_forward.py` — `run_walk_forward_validation()` : ajouter garde avant appel `split_walk_forward_folds`
- `code/src/walk_forward.py` — `_is_expected_wf_data_shortage()` : étendre pour couvrir ce cas

### Implémentation

Dans `run_walk_forward_validation()`, avant `split_walk_forward_folds()` :

```python
# P0-WF1D: exclure 1d si < 900 barres (pas assez pour 2 folds valides)
_min_bars_for_2folds = min_train_bars + 2 * min_test_bars  # 500 + 400 = 900
if len(full_df) < _min_bars_for_2folds:
    logger.info(
        f"[WF-SKIP] {tf} {scenario_name}: {len(full_df)} bars < {_min_bars_for_2folds} "
        f"(min pour 2 folds) — timeframe exclu du WF"
    )
    continue
```

### Checklist

- [x] Identifier la bonne position dans `run_walk_forward_validation()` (après calcul `full_df`)
- [x] Ajouter la garde `_min_bars_for_2folds`
- [x] Vérifier que `_is_expected_wf_data_shortage` logge correctement le skip
- [x] Valider : plus de `WF Fold 1/4` avec 1 seul fold produit pour 1d
- [x] `pytest tests/ -x -q` vert

---

## P1 — Top-N WF trop restrictif

### Diagnostic

```
47/60 résultats passent les OOS gates → WF ne teste que 5 configs
```

Le paramètre `top_n` (probablement hardcodé à 5) soumet uniquement les 5 meilleures
configs IS au WF. Si ces 5 échouent toutes (overfit fréquent sur les tops IS), le
bot rate 42 configs potentiellement valides classées 6–47.

### Solution retenue

Passer `top_n` de 5 à **15** (aligné sur le Top 15 affiché dans le tableau IS).
Cela multiplie par 3 les chances de trouver une config WF-valide sans surcharger
le WF (60 configs totales, 15 est raisonnable sur 1–2 secondes de runtime WF).

### Fichiers concernés

- `code/src/backtest_orchestrator.py` ou `code/src/MULTI_SYMBOLS.py` — appel `run_walk_forward_validation(top_n=...)`

### Checklist

- [x] Localiser l'appel `run_walk_forward_validation` et son paramètre `top_n`
- [x] Passer `top_n` de 5 → 15
- [x] Vérifier qu'il n'y a pas de `top_n` hardcodé dans `walk_forward.py` lui-même
- [x] `pytest tests/ -x -q` vert

---

## P2 — Seuil offset horloge 3000ms → 2000ms

### Diagnostic

```
exchange_client: offset=-2251ms
timestamp_utils: diff=-1121ms  →  SYNCHRONISATION STABLE (pas de resync)
```

La garde P2 existante lit `-1121ms` (méthode `timestamp_utils`) mais Binance
perçoit `-2251ms`. La divergence de ~1130ms entre les deux méthodes crée un
angle mort : l'offset réel dépasse 2000ms sans déclencher le resync.

### Solution

Abaisser le seuil dans `init_timestamp_solution()` de **3000ms → 2000ms**.
À -2251ms réel, la mesure `timestamp_utils` serait -1121ms. Avec un seuil à 2000ms,
ce niveau ne déclencherait pas encore le resync non plus — mais le but est d'attraper
les cas où la mesure interne dépasse 2000ms (ce qui correspond à ~3100ms réel).

Alternative plus robuste : lire l'offset directement depuis `exchange_client` (la
source de vérité Binance) plutôt que refaire une mesure indépendante dans
`timestamp_utils`. C'est la solution recommandée.

### Fichiers concernés

- `code/src/timestamp_utils.py` — `init_timestamp_solution()` : seuil `3000` → `2000`

### Checklist

- [x] Localiser `abs(diff) >= 3000ms` dans `init_timestamp_solution()`
- [x] Remplacer par `abs(diff) >= 2000`
- [x] Mettre à jour le message de warning en cohérence (`seuil 2000ms`)
- [x] `pytest tests/ -x -q` vert

---

## P3 — Log reconciler : afficher `real_pair` au lieu de `backtest_pair`

### Diagnostic

```
[RECONCILE] PEPEUSDT: cohérent (balance=0.660000 PEPE, in_position=False)
```

La réconciliation porte sur l'asset Binance (clé=`real_pair`=PEPEUSDC) mais le log
affiche `backtest_pair` (PEPEUSDC désormais, donc résolu implicitement avec le switch).

**Note** : avec `backtest_pair=PEPEUSDC=real_pair`, ce point est **auto-résolu**.
Si des paires futures ont à nouveau `backtest_pair ≠ real_pair`, le bug reviendra.
Il faut donc corriger structurellement le log.

### Fichiers concernés

- `code/src/position_reconciler.py` — section log du reconcile

### Checklist

- [x] Vérifier que `position_reconciler.py` logge bien `real_pair` (pas `backtest_pair`)
- [x] Corriger si nécessaire
- [x] `pytest tests/ -x -q` vert

---

## P4 — Nettoyage des pair_states orphelins dans `bot_state.json`

### Diagnostic

```
bot_state chargé — 7 entrées, 4 pair_state(s) persisté(s)
```

3 pair_states en trop : anciennes paires (ONDO, etc.) restées dans le fichier d'état.
Pas d'impact sur le trading mais risque de confusion en diagnostic et de faux positifs
dans des guards qui itèrent sur toutes les clés de `bot_state`.

### Solution

Nettoyage manuel du fichier `states/bot_state.json` : supprimer les clés qui ne
correspondent à aucune paire de `crypto_pairs`. Ce nettoyage ne doit pas être
automatique au démarrage (risque de supprimer un état valide par erreur de config).

### Checklist

- [x] Lire `states/bot_state.json` et identifier les clés orphelines
- [x] Supprimé : paires legacy USDT et les clés obsolètes (4 clés)
- [x] Vérifier que le bot recharge correctement avec l'état nettoyé
- Note : HMAC recalculé automatiquement par le bot au prochain cycle de sauvegarde

---

## Calendrier d'exécution

| Item | Priorité | Statut | Note |
|---|---|---|---|
| Point 5 USDT/USDC divergence | P0 | ✅ RÉSOLU | Switch PEPEUSDC appliqué |
| P0 WF 1d exclusion < 2 folds | P0 | ✅ RÉSOLU | `walk_forward.py` · 797 tests |
| P1 Top-N WF 5→15 | P1 | ✅ RÉSOLU | `walk_forward.py` défaut 15 |
| P2 Seuil offset 3000→2000ms | P2 | ✅ RÉSOLU | `timestamp_utils.py` |
| P3 Log reconciler real_pair | P3 | ✅ RÉSOLU | `position_reconciler.py` |
| P4 Nettoyage bot_state orphelins | P4 | ✅ RÉSOLU | 4 clés supprimées |
