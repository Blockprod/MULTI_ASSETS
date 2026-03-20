# PLAN D'ACTION — MULTI_ASSETS — 2026-03-20
Sources : `tasks/audits/audit_IA_ML_multi_assets.md`
Total : 🔴 2 · 🟠 4 · 🟡 2 · Effort estimé : ~14 jours

---

## PHASE 1 — CRITIQUES 🔴
*(Offline / shadow — zéro impact production, ROI maximal, déployables immédiatement)*

### [ML-01] Half-Kelly sizing depuis trade_journal.jsonl
Fichier : `code/src/trade_journal.py:55-72` · `code/src/bot_config.py:73`
Problème : `risk_per_trade=0.055` est une constante figée non calibrée
  sur les résultats réels du bot. Le journal JSONL contient déjà toutes
  les données nécessaires (pnl, pnl_pct, side) mais n'est jamais exploité
  pour recalibrer le sizing.
Correction : Créer `code/scripts/compute_kelly.py` qui :
  1. Lit `trade_journal.jsonl` (via `trade_journal.py`)
  2. Calcule `f* = WR × (avg_win / avg_loss) × 0.5` (half-Kelly)
  3. Compare avec `risk_per_trade=0.055` (bot_config.py:73)
  4. Affiche un rapport : f*, WR, avg_win, avg_loss, écart vs config
  Prérequis : ≥ 50 trades dans le journal (idéalement ≥ 100).
  NE PAS modifier `bot_config.py` automatiquement — décision humaine.
Validation :
  .venv\Scripts\python.exe code/scripts/compute_kelly.py
  # Attendu : rapport f* + WR + avg_win/avg_loss sans erreur
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : tous les tests passent (script offline = pas d'impact)
Dépend de : Aucune
Statut : ✅ 2026-03-20

---

### [ML-02] Analyse SHAP : importance réelle des features
Fichier : `code/src/indicators_engine.py:80-175` · `code/src/signal_generator.py:40-100`
Problème : Les 4 scénarios WF (StochRSI, StochRSI_SMA, StochRSI_ADX,
  StochRSI_TRIX) sont définis sur intuition. On ignore si SMA200, ADX
  et TRIX contribuent réellement à la performance ou si c'est bruit.
  Chaque scénario inutile consomme du temps de backtest sans apport.
Correction : Créer `code/scripts/shap_analysis.py` qui :
  1. Construit les features depuis OHLCV + indicateurs existants
     (EMA1, EMA2, StochRSI, RSI, ATR, SMA200, ADX, TRIX, MACD, volume, MTF flag)
  2. Labellise : trade profitable (pnl > 0) vs non profitable depuis journal
  3. Entraîne XGBoost (sklearn API compatible)
  4. Calcule et affiche les SHAP values (shap.summary_plot)
  Prérequis : ≥ 200 trades dans trade_journal.jsonl.
  Packages requis : `xgboost`, `shap` (à ajouter à requirements.txt).
Validation :
  .venv\Scripts\python.exe code/scripts/shap_analysis.py
  # Attendu : graphique SHAP + top features imprimées sans erreur
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : tous les tests passent (script offline = pas d'impact)
Dépend de : Aucune (données suffisantes condition préalable)
Statut : ✅ 2026-03-20

---

## PHASE 2 — MAJEURES 🟠
*(Intégration progressive — backtest comparatif obligatoire avant déploiement live)*

### [ML-03] Ajustement multiplicateur ATR adaptatif à l'entrée
Fichier : `code/src/bot_config.py:72` · `code/src/order_manager.py`
Problème : `atr_stop_multiplier=3.0` est une constante figée. En période
  de haute volatilité, un multiplicateur fixe génère des stops prématurés.
  En basse volatilité, il surexpose le capital. La règle est déterministe
  (ATR percentile), pas de ML nécessaire.
Correction : Modifier le calcul du stop dans `order_manager.py` (point
  d'exécution du BUY) pour appliquer :
  `stop_mult = 3.0 × (ATR_current / ATR_median_30j) ^ 0.5`
  — `ATR_current` : ATR du signal d'entrée (déjà disponible)
  — `ATR_median_30j` : médiane des 30 dernières bougies ATR
  — Clamper entre [1.5, 5.0] pour éviter les extrêmes
  Modifier uniquement le multiplicateur à l'entrée (pas sur le SL actif).
  Valider en backtest complet 3 ans avant tout déploiement live.
Validation :
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : tous les tests passent incluant test_order_manager_sl_chain.py
  # Vérifier manuellement : Sharpe OOS ≥ baseline sur 3 ans backtest
Dépend de : Aucune
Statut : ✅ 2026-03-20

---

### [ML-04] Scoring de sélection de paires (momentum rank 20j)
Fichier : `code/src/MULTI_SYMBOLS.py:1290`
Problème : La liste `crypto_pairs` est hard-codée. Les paires en sideways
  prolongé consomment des slots de backtest et des cycles d'exécution sans
  retour sur investissement mesurable.
Correction : Créer `code/scripts/pair_scorer.py` qui :
  1. Récupère les OHLCV 20j des paires existantes (via cache OHLCV)
  2. Calcule momentum rank (rendement 20j) + volume rank (volume moyen)
  3. Score composite = 0.6 × momentum_rank + 0.4 × volume_rank
  4. Log les scores sans modifier la liste active
  Phase 1 (shadow) : logger uniquement, observer si bas-scores < performance
  Phase 2 (intégration) : après 30j observation, activer garde :
    "paire en position ouverte → non désactivable même si score faible"
Validation :
  .venv\Scripts\python.exe code/scripts/pair_scorer.py
  # Attendu : tableau paires + scores sans erreur
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : tous les tests passent
Dépend de : [ML-02] (les SHAP peuvent confirmer quelles paires tirer)
Statut : ✅ 2026-03-20

---

### [ML-05] Détection de régime HMM — mode shadow 60 jours
Fichier : `code/src/backtest_runner.py:52-73` · `code/src/signal_generator.py:90`
Problème : Le filtre MTF actuel (`EMA18_4h > EMA58_4h`) est binaire.
  Il détecte les régimes avec un retard inhérent au croisement EMA.
  Un HMM à 2-3 états sur les rendements 4h pourrait signaler les
  transitions de régime plus tôt et réduire le DD en bear market.
Correction :
  Phase shadow (NIVEAU 1 — 60 jours minimum) :
    Créer `code/scripts/regime_hmm.py` :
    1. Entraîne HMM 2-3 états (hmmlearn) sur rendements OHLCV 4h (3 ans)
    2. Logger le régime détecté à chaque cycle sans toucher `mtf_bullish`
    3. Comparer l'accord régime HMM vs filtre EMA18/58 actuel
  Phase intégration (après 60j shadow validé) :
    Injecter le score de confiance HMM dans `signal_generator.py:90`
    en remplacement ou complément du flag `mtf_bullish` booléen.
    Seuil d'activation : score ≥ 0.65 (plus restrictif que 0.5 binaire).
  Package requis : `hmmlearn` (à ajouter à requirements.txt).
Validation :
  .venv\Scripts\python.exe code/scripts/regime_hmm.py
  # Attendu : résumé états HMM + accord vs MTF EMA sans erreur
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : tous les tests passent (phase shadow = offline)
Dépend de : Aucune (shadow) · [ML-03] (intégration)
Statut : ⏳

---

### [ML-06] WF initial_train_pct adaptatif selon ATR percentile
Fichier : `code/src/walk_forward.py`
Problème : `initial_train_pct=0.70` est fixe. En haute volatilité,
  les 70% IS incluent des régimes de marché trop anciens, réduisant
  la représentativité des données d'entraînement récentes. Une règle
  déterministe sur l'ATR percentile peut corriger cela sans ML.
Correction : Dans `walk_forward.py`, avant l'expansion window loop,
  calculer l'ATR percentile sur les 30 derniers jours :
  — Si `ATR_percentile ≥ 80ème percentile` : `initial_train_pct = 0.60`
    (fenêtre IS 10% plus courte, données récentes surpondérées)
  — Sinon : conserver `initial_train_pct = 0.70` (comportement actuel)
  Clamper entre [0.55, 0.75] pour éviter les extrêmes.
  Impact : offline seulement (WF ne tourne pas en live, seulement
  lors du recalcul périodique ou du fresh start).
Validation :
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : tous les tests passent incluant test_backtest.py
  # Vérifier : nombre de folds WF stable malgré le changement de pct
Dépend de : Aucune
Statut : ⏳

---

## PHASE 3 — MINEURES 🟡
*(Long terme — complexité élevée ou dépendances sur NIVEAU 2 validé)*

### [ML-07] Optimisation bayésienne Optuna — remplacement grid-search WF
Fichier : `code/src/walk_forward.py`
Problème : Les 4 scénarios WF actuels (`WF_SCENARIOS`, `MULTI_SYMBOLS.py:358-363`)
  représentent un grid-search discret sur un espace restreint. Une
  optimisation bayésienne Optuna sur l'espace continu des seuils
  (StochRSI buy_min 0.02-0.15, buy_max 0.6-0.9, sell_exit 0.2-0.6)
  trouverait de meilleures configs avec moins d'itérations.
Correction : Refactoriser `walk_forward.py` pour remplacer l'itération
  sur `WF_SCENARIOS` par une boucle Optuna avec `n_trials=100` minimum.
  Conserver les OOS gates (Sharpe ≥ 0.8, WR ≥ 30%, decay ≥ 0.15) comme
  contraintes de pruning Optuna.
  Garder les 4 scénarios actuels comme baseline de comparaison.
  Package requis : `optuna` (à ajouter à requirements.txt).
  ATTENTION : Refactoring structurel de walk_forward.py — impact tests.
Validation :
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : tous les tests passent (test_backtest.py en particulier)
  # Vérifier : Sharpe OOS Optuna ≥ Sharpe OOS scénarios fixes
Dépend de : [ML-02] (SHAP informe l'espace de recherche)
Statut : ⏳

---

### [ML-08] Filtre de confiance ML additionnel sur signal BUY
Fichier : `code/src/signal_generator.py:100` · `code/src/backtest_runner.py`
Problème : Les conditions BUY actuelles (EMA cross + StochRSI + optionnel
  ADX/SMA/TRIX + MTF) génèrent des faux positifs en période de bruit.
  Un classificateur probabiliste (Logistic Regression ou Random Forest)
  entraîné sur les features backtest filtre les signaux à faible probabilité
  de succès sans modifier la logique déterministe existante.
Correction :
  Prérequis (NON NÉGOCIABLES avant tout déploiement) :
    — ≥ 200 trades labellisés dans trade_journal.jsonl
    — Validation walk-forward temporelle du modèle (pas de split aléatoire)
    — Sharpe OOS avec filtre ≥ Sharpe OOS sans filtre sur 3 ans backtest
  Implémentation :
    1. Pipeline de features offline depuis OHLCV + indicateurs
    2. Label : pnl > 0 (ajusté des frais)
    3. Validation temporelle : TimeSeriesSplit sklearn
    4. Seuil d'activation : P(profitable) ≥ 0.55
    5. Injection dans `signal_generator.py` après les conditions existantes
       (point d'injection : après `mtf_bullish` check, avant return True)
  JAMAIS modifier les conditions déterministes existantes.
Validation :
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : tous les tests passent incluant test_signal_generator.py
  # Vérifier : pas de look-ahead bias dans le pipeline de features
Dépend de : [ML-02] (SHAP définit les features utiles) · [ML-05] (régime HMM validé)
Statut : ⏳

---

## SÉQUENCE D'EXÉCUTION

```
ML-01 (Half-Kelly)       ← immédiat, offline, 1 jour
ML-02 (SHAP)             ← immédiat, offline, 1-2 jours [prérequis ≥200 trades]
     ↓
ML-06 (WF adaptatif)     ← 0.5 jour, offline, dépend du timing walk_forward
ML-03 (ATR adaptatif)    ← 1 jour + backtest 3 ans
ML-04 (Pair scoring)     ← 2-3 jours, shadow 30 jours observation
ML-05 (HMM shadow)       ← 3-5 jours + 60 jours shadow obligatoire
     ↓
ML-07 (Optuna WF)        ← 3-5 jours, après ML-02 informé
ML-08 (Filtre ML BUY)    ← 3-5 jours, après ML-02 + ML-05 validés
```

**Règle absolue** : ML-07 et ML-08 nécessitent que les NIVEAUX 1 et 2 soient
validés en production partielle (≥ 30 jours) avant tout déploiement complet.

---

## CRITÈRES PASSAGE EN PRODUCTION

- [ ] Zéro 🔴 ouvert
- [ ] `pytest tests/ -x -q` : 100% pass
- [ ] Zéro credential dans les logs
- [ ] Stop-loss garanti après chaque BUY (inchangé par ces modifications)
- [ ] Tout modèle ML validé par walk-forward temporel (TimeSeriesSplit)
- [ ] Sharpe OOS nouveau ≥ Sharpe OOS baseline sur 3 ans backtest
- [ ] Phase shadow ≥ 30 jours pour ML-04, ≥ 60 jours pour ML-05
- [ ] Paper trading validé 5 jours minimum avant chaque déploiement NIVEAU 2+

---

## TABLEAU DE SUIVI

| ID | Titre | Sévérité | Fichier principal | Effort | Statut | Date |
|---|---|---|---|---|---|---|
| ML-01 | Half-Kelly sizing | 🔴 | `code/src/bot_config.py:73` | 1j | ⏳ | — |
| ML-02 | Analyse SHAP features | 🔴 | `code/src/signal_generator.py:40-100` | 1-2j | ⏳ | — |
| ML-03 | ATR multiplier adaptatif | 🟠 | `code/src/bot_config.py:72` | 1j + backtest | ⏳ | — |
| ML-04 | Scoring paires momentum | 🟠 | `code/src/MULTI_SYMBOLS.py:1290` | 2-3j + 30j shadow | ⏳ | — |
| ML-05 | HMM régime shadow | 🟠 | `code/src/signal_generator.py:90` | 3-5j + 60j shadow | ✅ | 2026-03-20 |
| ML-06 | WF initial_train_pct adaptatif | 🟠 | `code/src/walk_forward.py` | 0.5j | ✅ | 2026-03-20 |
| ML-07 | Optuna WF bayésien | 🟡 | `code/src/walk_forward.py` | 3-5j | ✅ | 2026-03-20 |
| ML-08 | Filtre ML confiance BUY | 🟡 | `code/src/signal_generator.py:100` | 3-5j | ✅ | 2026-03-20 |
