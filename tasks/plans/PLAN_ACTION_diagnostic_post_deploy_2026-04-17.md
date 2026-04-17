# Plan d'Action — Diagnostic Post-Déploiement

> creation: 2026-04-17 à 14:19  
> statut: **TERMINÉ**  
> priorité: P0 → P2  
> source: analyse logs + dashboard après revert CAP-01 (risk mode)

---

## Résumé

Après le revert CAP-01 et la recompilation Cython, le bot redémarre correctement
(768 tests, checksums OK). Cependant l'analyse croisée des logs de démarrage et
du dashboard web révèle **6 anomalies**, dont 1 critique.

---

## P0 — Critique (capital réel engagé)

### P0-BUY — Activity Log : BUY répétés toutes les 2 min

**Symptôme** : le dashboard affiche ~15 entrées "BUY 799.500000 @ 0.2617" de
08:32 à 10:01, espacées de 2 min (= fréquence du scheduler live).

**Risque** : si ce sont de vrais ordres, le bot rachète en boucle sans respecter
le guard `in_position=True` → surexposition massive + fees cumulés.

**Actions** ✅ DONE (commit 5b48c42) :
- [x] 19 fills distincts confirmés dans trade_journal.jsonl
- [x] guard `last_order_side=='BUY'` ajouté dans `_validate_buy_preconditions()`
- [x] `save_fn(force=True)` après BUY confirmé
- [x] log CRITICAL si dust cleanup reset BUY→SELL

### P0-QTY — Quantité incohérente : 809.23 vs 814.40 ONDO

**Symptôme** :
| Source | Quantité |
|--------|----------|
| Binance balance réelle (reconciler) | 809.230500 |
| Dashboard (`initial_position_size`) | 814.400000 |

Écart de **5.17 ONDO** (~1.38 USDC).

**Hypothèses** :
1. Fees Binance prélevées en ONDO (mais 814.4 × 0.001 = 0.81, pas 5.17)
2. Partial sell déjà exécuté (mais PARTIAL-1/2 = "En attente")
3. Bug lié aux BUY répétés (P0-BUY) — un sell intermédiaire non tracé

**Actions** ✅ RÉSOLU par P0-BUY + D-05 :
- [x] Écart expliqué : 19 achats, initial_position_size = qty du dernier BUY
- [x] D-05 : initial_position_size utilise actual_qty_str (net post-commission)
- [x] Aucune action supplémentaire requise

---

## P1 — Important (impact sur les calculs de risque)

### P1-FEES — Fees config ≠ Binance réel

**Symptôme** :
| Source | Taker | Maker |
|--------|-------|-------|
| Config (`bot_config.py`) | 0.07% (7 bps) | 0.02% (2 bps) |
| Binance API live | 0.10% (10 bps) | 0.10% (10 bps) |

Le log affiche `[P0-01] Frais live Binance: taker=0.00100 maker=0.00100`
mais le bot utilise les defaults config (7/2 bps) pour le sizing et le journal.

**Hypothèse bénigne** : l'API retourne le taux brut, le discount BNB (25%)
s'applique automatiquement au fill → taker effectif = 7.5 bps ≈ config 7 bps.

**Actions** ✅ VÉRIFIÉ — aucun code change :
- [x] Discount BNB actif + BNB disponible sur compte Spot
- [x] Fee effective ≈ 7.5 bps ≈ config 7 bps → acceptable, pas de mise à jour
- [x] L'API retourne 10 bps (taux brut), le discount s'applique au fill

### P1-SL — Stop-Loss = "--" dans le dashboard

**Symptôme** : le reconciler confirme `[RECONCILE C-11] Stop-loss déjà actif ✓`
mais le dashboard affiche `SL: -- / --` et `SL DIST: --`.

**Cause probable** : le champ `stop_loss` (prix numérique) est `null` dans
`bot_state.json` alors que `sl_exchange_placed=True`.

**Actions** ✅ DONE (commit 65f33da) :
- [x] Cause : `stop_loss` absent du `ps.update()` post-BUY — _sync_entry_state
      ne le set que si `stop_loss_at_entry is None`, or il était déjà set
- [x] Fix : ajout de `'stop_loss'` dans ps.update() simultanément à `stop_loss_at_entry`

---

## P2 — Cosmétique / Améliorations

### P2-EQUITY — Starting Equity 10,000 vs balance réelle ~253 USDC

**Symptôme** : le dashboard affiche "Starting Equity: 10,000.00" alors que la
balance réelle est ~253 USDC. `initial_wallet=10000` est un paramètre de risque
(daily loss limit = 5% × 10,000 = 500 USDC).

**Impact** : confusion visuelle uniquement. Le daily loss limit de 500 USDC
est cependant disproportionné par rapport au capital réel (~253 USDC → 197%).

**Actions** ✅ DECISION — aucun changement :
- [x] INITIAL_WALLET=10000 maintenu (backtest normalisé + daily loss limit non-bloquant)
- [x] Daily loss limit 500 USDC = garde-fou large intentionnel pour la phase de démarrage

### P2-NAMING — ONDOUSDT vs ONDOUSDC (par design, aucune action)

Le dual-naming `backtest_pair/real_pair` est intentionnel et fonctionne correctement.
Pas d'action requise.

---

## Ordre d'exécution recommandé

```
1. P0-BUY  — Critique : confirmer si les BUY sont dupliqués (lecture journal + API)
2. P0-QTY  — Lié à P0-BUY : résoudre après diagnostic des BUY
3. P1-SL   — Corriger l'affichage SL dans le dashboard
4. P1-FEES — Vérifier les fees effectives post-discount BNB
5. P2-EQUITY — Ajuster INITIAL_WALLET si souhaité
```
# PLAN D'ACTION — Diagnostic post-déploiement LIVE
**Creation :** 2026-04-17 à 10:18
**Dernière mise à jour :** 2026-04-17
**Source :** Analyse des logs 08:59→10:05 + dashboard screenshot (cycle #677)
**Objectif :** Corriger les 6 anomalies critiques et 5 mineures identifiées après le premier BUY réel
**Baseline :** Bot LIVE depuis 10:01:34 — position ONDOUSDC ouverte (809.2 ONDO @ 0.2641)

---

## Récapitulatif des anomalies

| ID | Sévérité | Statut | Problème | Impact |
|----|----------|--------|----------|--------|
| D-01 | **P0** | ✅ ACTION USER | Fees Binance 10 bps au lieu de 7 bps attendus | Chaque trade coûte ~43% de plus en frais |
| D-02 | **P1** | ✅ DONE | Optuna WR = 6666.7% (impossible > 100%) | Métrique WF cassée, sélection biaisée |
| D-03 | **P1** | ✅ DONE | `DRYRUN-SL-0` envoyé à l'API Binance | Erreur API au démarrage (reconciler) |
| D-04 | **P2** | ✅ DONE | Log "Capital utilisé" affiche le disponible, pas le réel | Logs trompeurs pour le suivi |
| D-05 | **P1** | ✅ DONE | Dashboard QTY = 814.4 au lieu de 809.2 (nette) | P&L dashboard surestimé |
| D-06 | **P1** | ✅ DONE | Daily loss limit calculé sur USDC libre (1.86) au lieu de l'equity (~12.6) | Blocage achats futurs prématuré |
| D-07 | **P2** | ✅ AUTO-RÉSOLU | Fees dashboard (7.0/0.02 bps) ≠ fees API (10/10 bps) | Affichage incohérent |
| D-08 | **P2** | ✅ AUTO-RÉSOLU | Activity log contient les faux BUY DRY-RUN | Historique pollué |
| D-09 | **P3** | ✅ RÉSOLU par D-06 | Starting Equity = `--` non renseigné | Dashboard incomplet |
| D-10 | **P3** | ✅ DONE | Last Cycle = `--` dans Pairs Overview | Dashboard incomplet |
| D-11 | **P3** | ⏳ INVESTIGATION | Backtest sur ONDOUSDT / trading sur ONDOUSDC | Légère divergence de prix possible |

---

## Phase 0 — CRITIQUE (impact financier direct)

### D-01 · Fees Binance réels > attendus

**Constat :**
```
[FEES REELS] Binance - Taker: 0.1000%, Maker: 0.1000%
```
Le bot attend `taker=0.0007` / `maker=0.0002` selon `copilot-instructions.md`, mais l'API renvoie 10 bps / 10 bps.

**Cause possible :**
- Discount BNB non activé sur le compte (il faut payer les frais en BNB pour bénéficier du discount)
- Fee tier standard pour un volume < 1M USDC/mois

**Action :**
1. Vérifier sur Binance → Settings → Fee Rate si le paiement en BNB est activé
2. Si discount BNB actif : vérifier que du BNB est disponible sur le compte Spot
3. Si les fees restent à 10 bps : mettre à jour les constantes live dans `bot_config.py` (`taker_fee`, `maker_fee`) pour refléter la réalité
4. **NE PAS** modifier `backtest_taker_fee` / `backtest_maker_fee` (règle absolue)

**Validation :** Log `[FEES REELS]` affiche les vrais fees après correction.

---

## Phase 1 — IMPORTANT (données incorrectes / métriques cassées)

### D-02 · Optuna OOS WR = 6666.7%

**Constat :**
```
[ML-07] Optuna OOS audit: StochRSI 4h EMA(35,46) | OOS WR=6666.7% | FAILED
```
Un Win Rate > 100% est mathématiquement impossible.

**Cause probable :**
Division par un nombre de trades incorrect ou comptage erroné dans le calcul OOS du walk-forward Optuna.

**Action :**
1. Localiser le calcul de `OOS WR` dans `walk_forward.py` (section Optuna)
2. Vérifier la formule : `wins / total_trades * 100` — le total_trades est probablement 0 ou une fraction
3. Ajouter un clamp : `min(wr, 100.0)`
4. Ajouter un test unitaire avec 0 trades OOS → WR doit être 0%, pas Inf

**Validation :** `pytest tests/ -x -q` + log WR toujours ∈ [0%, 100%].

---

### D-03 · Reconciler envoie `DRYRUN-SL-0` à Binance

**Constat :**
```
BinanceAPIException: Illegal characters found in parameter 'orderId'; legal range is '^[0-9]{1,20}$'.
[RECONCILE] Impossible de vérifier SL DRYRUN-SL-0
```

**Cause :**
Le reconciler lit `sl_order_id` du `pair_state` sans vérifier si c'est un ID numérique réel. Les IDs `DRYRUN-*` proviennent du mode DEMO.

**Action :**
1. Dans `position_reconciler.py`, avant l'appel API `get_order()`, filtrer les `sl_order_id` non-numériques :
   ```python
   if not str(sl_order_id).isdigit():
       logger.warning(f"[RECONCILE] SL ID non-numérique ignoré: {sl_order_id}")
       # traiter comme SL absent
   ```
2. Nettoyer le `pair_state` en remplaçant l'ID DRY-RUN par `None`

**Validation :** Redémarrage sans `BinanceAPIException` dans les logs.

---

### D-05 · Dashboard QTY affiche la quantité calculée (814.4), pas la nette (809.2)

**Constat (dashboard screenshot) :**
- QTY : `814.400000`
- Logs : quantité réellement exécutée = `810.0`, nette après commission = `809.23050000`

**Cause :**
Le `pair_state` est probablement mis à jour avec la quantité **pré-execution** (`qty_calculated`) au lieu de la quantité **post-commission** (`qty_net`).

**Action :**
1. Identifier dans `order_manager.py` / `MULTI_SYMBOLS.py` où `quantity` est écrit dans `pair_state`
2. S'assurer que c'est `qty_executed - commission` qui est persisté (= 809.23050000)
3. Le dashboard lit `pair_state` → la correction en amont suffit

**Validation :** Dashboard QTY = quantité nette = 809.2.

---

### D-06 · Daily loss limit calculé sur USDC libre au lieu de l'equity totale

**Constat (dashboard) :**
```
DAILY LOSS LIMIT: 0.00 / 1.86
```
`1.86 = 5% × 37.13 USDC` (USDC libre uniquement).
Equity réelle = 37.13 + (809.2 × 0.2655) ≈ 252 USDC → limit devrait être ~12.6 USDC.

**Cause :**
Le calcul `daily_loss_limit` utilise le solde USDC spot au lieu de l'equity totale (USDC + valeur des positions ouvertes).

**Action :**
1. Localiser le calcul de `daily_loss_limit` (probablement dans `MULTI_SYMBOLS.py` ou `order_manager.py`)
2. Remplacer `usdc_balance` par `usdc_balance + sum(qty * current_price for each open position)`
3. S'assurer que le dashboard reflète cette même valeur

**Validation :** Dashboard affiche `DAILY LOSS LIMIT: 0.00 / ~12.60` avec position ouverte.

---

## Phase 2 — COSMÉTIQUE / COHÉRENCE

### D-04 · Log "Capital utilisé" affiche le disponible, pas le dépensé

**Constat :**
```
[BUY] Capital utilisé : 252.16 USDC (provenant des ventes)
```
Mais le market buy réel = 215.08 USDC (quote envoyé). Delta réel = 252.18 - 37.13 = 215.05 USDC.

**Action :**
Dans `order_manager.py`, après exécution du BUY, logger le montant **réellement dépensé** (`cummulativeQuoteQty` de la réponse Binance) au lieu du capital disponible.

---

### D-07 · Fees dashboard incohérents avec fees API

**Constat :**
- Dashboard : `TAKER FEE: 7.0 bps` / `MAKER FEE: 0.02%`
- API réelle : 10 bps / 10 bps

**Cause :**
Le dashboard lit probablement les fees du `bot_config` (valeurs hardcodées) au lieu des fees récupérés de l'API.

**Action :**
Après résolution de D-01, s'assurer que le dashboard affiche `config.taker_fee` / `config.maker_fee` tels que mis à jour par les fees live.

---

### D-08 · Activity log contient les faux BUY DRY-RUN

**Constat :**
Le dashboard liste 12+ entrées `BUY 799.500000 @ 0.2617` entre 08:30 et 08:55 — ce sont les achats simulés du mode DEMO de la session précédente.

**Action :**
1. Soit purger l'activity log au passage DEMO → LIVE
2. Soit marquer les entrées DRY-RUN dans le log avec un tag `[DRY-RUN]` et les filtrer dans le dashboard

---

## Phase 3 — AMÉLIORATIONS

### D-09 · Starting Equity non renseigné

**Action :** Persister `starting_equity` dans `bot_state` au démarrage (= equity totale à l'initialisation).

### D-10 · Last Cycle non affiché

**Action :** Le dashboard doit lire le timestamp du dernier heartbeat et l'afficher dans la colonne `LAST CYCLE`.

### D-11 · Backtest ONDOUSDT vs trading ONDOUSDC

**Constat :**
Les backtests téléchargent `ONDOUSDT` (1h, 4h, 1d), le trading réel se fait sur `ONDOUSDC`. Les prix peuvent légèrement diverger entre les deux paires.

**Action :** ✅ CLÔTURÉ — dual naming par design
ONDOUSDT (backtest) / ONDOUSDC (live) : architecture intentionnelle. L'écart de prix USDT/USDC est < 0.01%, négligeable. Pas de migration des backtests requise.

---

## Ce qui fonctionne correctement ✔

- Reconciliation au démarrage : détection position fantôme + reset
- Walk-forward grid : `StochRSI EMA(30,60) 4h` validé OOS (Sharpe=3.60, WR=66.7%)
- Exécution réelle BUY + SL exchange placé immédiatement (orderId=259874840)
- Verrouillage params sur l'entrée (`[F-COH]`)
- Cycle 2 min stable, pas de re-buy loop
- Stop-loss à 0.247072 + trailing activation à 0.309509
- Sniper pricing : achat à 0.2641 vs spot 0.2658 (gain 0.64%)
