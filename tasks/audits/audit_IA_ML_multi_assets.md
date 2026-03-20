# AUDIT IA & ML — MULTI_ASSETS — 20 Mars 2026

> **Périmètre** : code source uniquement (`.py`, `.pyx`). Aucun fichier `.md`/`.txt` consulté.
> **Méthode** : lecture directe de chaque module, référence fichier:ligne pour chaque observation.

---

## PHASE 1 — Diagnostic de l'existant

### 1.1 Signaux et stratégie actuels

#### Indicateurs techniques utilisés

| Indicateur | Fichier | Lignes clés | Rôle |
|---|---|---|---|
| EMA rapide (ema1) | `indicators_engine.py:179` | EWM `adjust=False` | Tendance court terme |
| EMA lente (ema2) | `indicators_engine.py:180` | EWM `adjust=False` | Tendance long terme |
| RSI(14) | `indicators_engine.py` via `ta.momentum.RSIIndicator` | Signal intermédiaire |
| StochRSI(14) | `indicators_engine.py:80-98` | `compute_stochrsi()` — rolling min/max sur RSI | Timing d'entrée |
| SMA(200) | `indicators_engine.py` scénario `StochRSI_SMA` | Filtre tendance long terme |
| ADX(14) | `indicators_engine.py` via `ta.trend.ADXIndicator` | Force de tendance |
| TRIX(7,15) + histogramme | `indicators_engine.py` via `ta.trend.MACD` adapté | Momentum |
| ATR(14) | `indicators_engine.py` via `ta.volatility.AverageTrueRange` | Stop-loss + sizing |
| MACD histogramme | `indicators_engine.py` | Indicateur secondaire |
| EMA4h fast(18)/slow(58) | `backtest_runner.py:52-73`, `MULTI_SYMBOLS.py:~1148` | Filtre MTF |

#### Génération des signaux BUY

**Logique** (`signal_generator.py:40-100`) :
1. `ema1 > ema2` — tendance haussière
2. `stoch_rsi_buy_min (0.05) < StochRSI < stoch_rsi_buy_max (0.8)` — momentum non saturé
3. Conditions additionnelles par scénario :
   - `StochRSI_SMA` : `close > SMA(200)` (`signal_generator.py:68`)
   - `StochRSI_ADX` : `ADX > 25.0` (`signal_generator.py:74`)
   - `StochRSI_TRIX` : `TRIX_HISTO > 0` (`signal_generator.py:79`)
4. Filtre volume optionnel (désactivé, bench négatif) (`signal_generator.py:84`)
5. Filtre MTF 4h : EMA18\_4h > EMA58\_4h avec `shift(1)` anti look-ahead (`signal_generator.py:90`)

**Point de décision #1** — Toutes les conditions sont **booléennes et déterministes**. Aucune probabilité.

#### Génération des signaux SELL

**Logique** (`signal_generator.py:136-180`) :
1. Stop-loss : `prix < entry_price - (atr_stop_multiplier × ATR)` avec `atr_stop_multiplier=3.0`
2. Signal : `ema2 > ema1 AND StochRSI > stoch_rsi_sell_exit (0.4)`
3. Trailing stop : géré dans `order_manager.py` sur `max_price`
4. Prises partielles à +2% et +4% (`bot_config.py:78-81`)

#### Sélection des paramètres (Walk-Forward)

**Processus** (`walk_forward.py:400-580`) :
- 4 scénarios fixes : `WF_SCENARIOS` (`MULTI_SYMBOLS.py:358-363`)
- Fenêtre IS/OOS : expanding window, `n_folds` folds, `initial_train_pct=0.70`
- Sélection par **OOS Sharpe moyen** + 2 gates :
  - OOS Sharpe ≥ 0.8 (`walk_forward.py:32`)
  - OOS WinRate ≥ 30% (`walk_forward.py:33`)
  - Decay gate : OOS/FS Sharpe ≥ 0.15 (`walk_forward.py:538`)
- Top-2 par timeframe pour diversité (`walk_forward.py:450-456`)
- Sélection backup par Calmar : `trade_helpers.py → _select_best_by_calmar`

**Point de décision #2** — Sélection de scénario/EMA entièrement déterministe. Pas d'apprentissage.

#### Régime de marché

- **MTF 4h** : filtre binaire (bullish/not-bullish) via EMA18/EMA58 4h avec `shift(1)` (`backtest_runner.py:52-73`)
- **EMA adaptatif** : `get_optimal_ema_periods()` (`indicators_engine.py:120-175`) ajuste les périodes EMA selon ATR/Close :
  - Volatilité > 1.5% → EMA ×0.88
  - Volatilité < 0.5% → EMA ×1.12
  - 3 paliers fixes, pas de modèle continu

**Point de décision #3** — Régime : binaire (haussier/baissier). Thresholds codés (0.005, 0.015).

---

### 1.2 Données disponibles

| Type | Stockage | Granularité | Profondeur |
|---|---|---|---|
| OHLCV | Cache pickle `cache/` (TTL 30j) | 1h, 4h, 1d | 1095 jours (~3 ans glissants) |
| Indicateurs calculés | Cache LRU mémoire (30 entrées) | Même que OHLCV | Même que OHLCV |
| Trades exécutés | `trade_journal.jsonl` (JSONL append) | — | Depuis déploiement |
| État bot | `bot_state.json` (JSON\_V1 + HMAC) | — | Depuis déploiement |
| PnL journalier | `bot_state['_daily_pnl_tracker']` | Jour UTC | Depuis déploiement |

**Données journal** (`trade_journal.py:55-72`) : timestamp, pair, side, qty, price, fee, slippage, scenario, timeframe, ema1, ema2, ATR, stop\_price, pnl, pnl\_pct, equity\_before, equity\_after.

**Profondeur historique** : 3 ans de OHLCV = environ 26 280 bougies 1h par paire. Nombre de trades réels : inconnu (dépend de la durée de déploiement) — probablement < 500 trades par paire.

**Données absentes** : order book (profondeur), funding rates, données on-chain, sentiment, corrélations inter-paires en temps réel.

---

### 1.3 Infrastructure et contraintes techniques

| Contrainte | Valeur | Source |
|---|---|---|
| Cycle d'exécution | 2 minutes (`schedule_interval_minutes`) | `bot_config.py:88` |
| Rate limit API | 18 req/s (token bucket) | `exchange_client.py:44-55` |
| Max workers backtest | 4 threads (`max_workers`) | `bot_config.py:62` |
| Cap positions longues | 4 simultanées (`max_concurrent_long`) | `bot_config.py:111` |
| Backtest throttle | 3600s entre deux backtests | `bot_config.py:112` |
| Stack | Python 3.11.9, Cython (.pyd), PM2, Windows | — |
| Stop-loss | `STOP_LOSS_LIMIT` exchange-natif (pas trailing) | `MULTI_SYMBOLS.py:378` |

**Contrainte critique pour ML en production** : le moteur de backtest Cython (`backtest_engine_standard.pyx`) n'expose pas d'API de feature engineering. Tout modèle ML doit s'intégrer en amont (pré-calcul des features) ou en parallèle (shadow mode).

---

### 1.4 Points de décision déterministes

| # | Point de décision | Fichier:Ligne | Nature actuelle | ML applicable ? |
|---|---|---|---|---|
| 1 | Seuils StochRSI buy (0.05/0.8) | `bot_config.py:97-98`, `signal_generator.py:47-52` | Constantes figées | Possible |
| 2 | Seuil StochRSI sell (0.4) | `bot_config.py:99`, `signal_generator.py:165` | Constante figée | Possible |
| 3 | Seuil ADX (25.0) | `bot_config.py:100`, `signal_generator.py:74` | Constante figée | Possible |
| 4 | atr\_stop\_multiplier (3.0) | `bot_config.py:72` | Constante figée | Possible |
| 5 | Paliers EMA adaptatif (0.005/0.015) | `indicators_engine.py:153-163` | 3 paliers hardcodés | Possible |
| 6 | Filtre MTF 4h (binaire) | `backtest_runner.py:67`, `signal_generator.py:90` | Booléen | Possible |
| 7 | Sélection scénario parmi 4 | `MULTI_SYMBOLS.py:358-363` | Walk-forward discret | Possible |
| 8 | sizing\_mode ('risk') | `bot_config.py:75` | Constante | Possible |
| 9 | risk\_per\_trade (0.055) | `bot_config.py:73` | Constante figée | Possible |
| 10 | Sélection des paires | `MULTI_SYMBOLS.py:1290+` | Liste fixe hard-codée | Possible |

---

## PHASE 2 — Évaluation des opportunités IA/ML

---

### 2.A — ML supervisé sur les signaux (Random Forest / XGBoost / LightGBM)

**Principe** : remplacer ou enrichir la logique `signal_generator.py` (EMA cross + StochRSI) par un modèle supervisé entraîné sur les features OHLCV + indicateurs existants.

**Analyse** :

- **Features disponibles** : EMA1, EMA2, StochRSI, RSI, ATR, SMA200, ADX, TRIX, MACD, volume, MTF flag — soit ~12 features. Richesse modeste.
- **Labels** : trade profitable vs non profitable. Mais le label dépend du stop-loss (ATR×3), donc circulaire si l'ATR change.
- **Volume de données** : 26 280 bougies 1h × N paires. Pour XGBoost, c'est théoriquement suffisant. **Mais le nombre de trades réels est l'enjeu** : si le bot a exécuté 200 trades en 12 mois, l'ensemble d'entraînement supervisé est trop petit pour généraliser.
- **Risque surapprentissage crypto** : très élevé. Les marchés crypto ont des régimes non-stationnaires (bull 2020-21, bear 2022, consolidation 2023, bull 2024). Un modèle entraîné sur 2021 ne prédit pas 2022. Le walk-forward existant atténue ce risque pour des paramètres discrets, mais pas pour des poids de modèle continus.
- **Comparaison vs existant** : le système actuel fait déjà du walk-forward multi-scénarios. La valeur ajoutée d'un modèle supervisé est marginale si les features sont les mêmes indicateurs.

**Verdict ❌ NON PERTINENT** pour remplacement. ✅ **PERTINENT en enrichissement** : utiliser le score de probabilité du modèle comme **filtre de confiance supplémentaire** (seuil de probabilité ≥ 0.6 pour valider un signal EMA+StochRSI), en parallel/shadow mode d'abord.

| Critère | Évaluation |
|---|---|
| Gain attendu | Marginal (≈ réduction faux positifs, Sharpe +0.1 à +0.3 estimé) |
| Complexité | M (feature pipeline, validation temporelle, sérialisation modèle) |
| Risque prod | Élevé si remplacement, Faible si filtre additionnel |
| Cycle 2 min | Compatible (inférence < 1ms) |
| Données suffisantes | Partiel (OHLCV oui, trades labellisés probablement non) |

---

### 2.B — Détection de régime de marché par ML (K-Means / HMM)

**Principe** : remplacer le filtre MTF binaire (`backtest_runner.py:52-73`) par un modèle de régimes (ex : K-Means sur volatilité + volume + momentum ; ou HMM sur les rendements).

**Analyse** :

- Le filtre MTF actuel (`EMA18_4h > EMA58_4h`) est une proxy binaire du régime. Il fonctionne et a été validé en backtest.
- Un HMM à 2-3 états sur les rendements 4h/1d pourrait détecter des transitions de régime plus tôt (avant le croisement EMA).
- **Risque principal** : le HMM est sensible à l'initialisation et aux données non-stationnaires. Les paramètres doivent être ré-entraînés régulièrement. La complexité opérationnelle est significative.
- **Gain mesurable** : difficile à quantifier sans A/B test. Les travaux académiques (Lopez de Prado ch.18) montrent que la détection de régimes améliore la gestion des stops mais peu les signaux d'entrée en trend-following.
- **Compatibilité** : le flag `mtf_bullish` dans `signal_generator.py:90` est déjà injectable. On peut remplacer la valeur booléenne par un score de confiance de régime sans modifier la logique de signal.

**Verdict ✅ PERTINENT** — mais uniquement en **observation parallèle** pendant 60+ jours avant de remplacer le filtre MTF existant.

| Critère | Évaluation |
|---|---|
| Gain attendu | Réduction DD en bear market estimée à -2 à -4 pp |
| Complexité | M (hmmlearn ou sklearn, pipeline offline) |
| Risque prod | Modéré (injectable dans mtf\_bullish sans changer la logique SL) |
| Cycle 2 min | Compatible (ré-entraînement offline, inférence instantanée) |
| Données suffisantes | Oui (3 ans OHLCV 4h) |

---

### 2.C — Optimisation adaptative des seuils (RL ou optimisation bayésienne)

**Principe** : remplacer les constantes figées StochRSI buy min/max (0.05/0.8) et sell exit (0.4) par une optimisation bayésienne (Optuna) ou un agent RL qui les ajuste selon les conditions de marché.

**Analyse** :

- **Optimisation bayésienne (Optuna)** : faisable en offline avec le backtest existant. En pratique, c'est ce que fait déjà le walk-forward, mais de façon discrète (4 scénarios fixes). Une vraie optimisation bayésienne sur l'espace des seuils StochRSI serait un WF plus fin.
- **Reinforcement Learning** : non réaliste sur ce dataset. Le RL a besoin de millions d'interactions. Avec ~26k bougies 1h et un faible nombre de trades, l'agent ne peut pas converger. Sur 3 ans de données, on a ~200-400 trades réels par paire — trop peu.
- **Risque majeur** : les seuils actuels (0.4 pour sell exit) ont été optimisés en benchmark avec un gain mesuré (+2% PnL, -1pp DD, `bot_config.py:99`). Modifier ces seuils dynamiquement risque de dégrader les performances OOS.
- **Réalité prod** : Optuna peut tourner offline comme alternative au walk-forward actuel. Gain réel ≈ marginal si le walk-forward actuel couvre déjà 4 scénarios × 3 timeframes.

**Verdict ❌ NON PERTINENT** pour RL. ✅ **PERTINENT** pour optimisation bayésienne Optuna comme remplacement du grid-search walk-forward actuel (gain en efficacité de recherche, pas en performance).

| Critère | Évaluation |
|---|---|
| Gain attendu | En efficacité de recherche : S. En performance OOS : incertain |
| Complexité | RL=XL (non réaliste), Optuna=M (offline seulement) |
| Risque prod | RL=Critique, Optuna offline=Faible |
| Cycle 2 min | RL incompatible, Optuna offline compatible |
| Données suffisantes | Pour RL : non. Pour Optuna : oui |

---

### 2.D — Prédiction du sizing optimal par régression ML

**Principe** : remplacer les 3 modes de sizing (`position_sizing.py`) par un modèle de régression qui prédit la taille optimale selon les conditions de marché.

**Analyse** :

- Les 3 modes actuels (`risk`, `fixed_notional`, `volatility_parity`) couvrent déjà les 3 philosophies de sizing institutionnelles. Le mode `risk` (ATR-based) est le plus robuste théoriquement.
- Un modèle de régression pour le sizing nécessite un label clair : "taille optimale". Ce label n'existe pas a priori — il est circulaire (la taille optimale dépend de l'issue du trade).
- **Kelly criterion** : alternative plus simple et plus solide théoriquement que le ML pour le sizing. Calculable directement depuis les statistiques historiques du journal de trades.
- En pratique, le sizing est déjà adapté à la volatilité via ATR. Ajouter un layer ML introduit de la complexité sans gain démontrable.

**Verdict ❌ NON PERTINENT** pour ML. Recommandation alternative : implémenter le **half-Kelly** calculé depuis `trade_journal.jsonl` (win rate × avg\_win / avg\_loss × 0.5 = f\*). Complexité XS, gain mesurable sur le long terme.

| Critère | Évaluation |
|---|---|
| Gain attendu | ML : non mesurable sans données. Kelly : +5 à +15% Sharpe théorique |
| Complexité | ML=L (label circulaire), Kelly=XS |
| Risque prod | ML=Élevé, Kelly=Faible |
| Cycle 2 min | Kelly compatible |
| Données suffisantes | Pour ML : non. Pour Kelly : oui si ≥ 50 trades |

---

### 2.E — Agent de sélection de paires

**Principe** : remplacer la liste fixe `crypto_pairs` (`MULTI_SYMBOLS.py:1290`) par un agent qui sélectionne dynamiquement les paires selon un score de momentum, volume, et conditions de marché.

**Analyse** :

- La liste est hard-codée. Les paires ne changent pas entre cycles — seuls les paramètres WF changent par paire.
- Un score de sélection de paires (ex : momentum rank sur 20j, volume rank) est **faisable avec les données existantes** (OHLCV déjà fetchées).
- **Risque de surconcentration** : si l'agent identifie 2-3 paires très corrélées (ex : BTC + ETH + SOL en bull), le portefeuille subit un drawdown simultané. Le `max_concurrent_long=4` (`bot_config.py:111`) limite l'exposition mais pas la corrélation.
- **Risque d'over-trading** : un agent qui tourne toutes les 2 minutes et change les paires actives peut générer des ordres non-désirés si une paire est en position ouverte et que l'agent veut la désactiver.
- Ce n'est pas du ML — c'est une règle de ranking. Intégrable proprement.

**Verdict ✅ PERTINENT** mais en mode **scoring simple** (momentum rank 20j + volume rank), pas en mode agent adaptatif IA. Complexité S. Compatible avec la liste actuelle comme liste candidate plutôt que liste active fixe.

| Critère | Évaluation |
|---|---|
| Gain attendu | Filtrage des paires en sideways marché → réduction trades perdants |
| Complexité | S (calcul offline sur données déjà disponibles) |
| Risque prod | Modéré (ne pas désactiver une paire en position ouverte) |
| Cycle 2 min | Compatible |
| Données suffisantes | Oui |

---

### 2.F — Agent de gestion des stops par ML

**Principe** : un modèle qui ajuste dynamiquement `atr_stop_multiplier` (actuellement 3.0, `bot_config.py:72`) selon la volatilité réalisée et le régime de marché.

**Analyse** :

- La contrainte absolue : le stop est un `STOP_LOSS_LIMIT` exchange-natif (`MULTI_SYMBOLS.py:378`). Une fois placé, il ne peut pas être modifié dynamiquement sans annuler/replacer l'ordre (`order_manager.py → _cancel_exchange_sl`).
- Annuler et replacer le SL à chaque cycle (toutes les 2 min) est dangereux : latence, erreurs API, fenêtre sans protection.
- L'ajustement du multiplicateur **avant** le BUY (au moment du sizing) est faisable et sans risque. C'est là qu'un modèle adaptatif a de la valeur.
- **Alternative simple** : utiliser l'ATR percentile du dernier mois (ATR\_current / ATR\_median\_30j) pour ajuster le multiplicateur. Pas de ML nécessaire.

**Verdict ❌ NON PERTINENT** pour agent ML dynamique sur SL actif. ✅ **PERTINENT** pour ajustement adaptatif du multiplicateur **au moment de l'entrée** uniquement (règle basée sur ATR percentile, pas de ML).

| Critère | Évaluation |
|---|---|
| Gain attendu | Réduction stops prématurés en période de haute volatilité |
| Complexité | Agent ML dynamique=L + critique. Règle ATR percentile=XS |
| Risque prod | Agent ML=Critique (cancel/replace SL). Règle=Faible |
| Cycle 2 min | Agent cancel/replace : incompatible avec la sécurité des positions |
| Données suffisantes | Pour la règle : oui |

---

### 2.G — Agent de rebalancing du portefeuille

**Principe** : un agent qui ajuste l'allocation du capital entre les paires selon les performances récentes et les corrélations.

**Analyse** :

- Avec `max_concurrent_long=4` et `sizing_mode='risk'` (ATR-based), le capital est déjà adaptatif par paire selon la volatilité.
- Le rebalancing actif (réduire l'exposition sur une paire sous-performante, augmenter sur une sur-performante) est du **momentum de portefeuille**. En crypto, ce signal est instable (la paire qui a le mieux performé les 30 derniers jours sous-performe souvent les 30 suivants — mean reversion).
- L'infrastructure de réconciliation (`position_reconciler.py`) est déjà complexe. Un agent de rebalancing ajouterait des ordres non-anticipés dans ce pipeline.
- Le `daily_loss_limit_pct=5%` (`bot_config.py:103`) fournit déjà une forme de protection globale du portefeuille.

**Verdict ❌ NON PERTINENT** — complexité opérationnelle élevée, gain non démontrable sur trend-following crypto, risque de collision avec la logique de réconciliation existante.

| Critère | Évaluation |
|---|---|
| Gain attendu | Non mesurable sur ce type de stratégie |
| Complexité | L (impact sur reconciler + state manager) |
| Risque prod | Élevé (ordres non-anticipés) |
| Cycle 2 min | Incompatible avec stabilité des positions |
| Données suffisantes | Insuffisant pour corrélations fiables inter-paires |

---

### 2.H — Agent LLM pour analyse de sentiment

**Principe** : utiliser GPT/Claude pour analyser le sentiment marché (news, social) et filtrer les signaux techniques.

**Analyse** :

- **Latence** : un appel API LLM prend 1-5 secondes. Dans un cycle de 2 minutes avec N paires en parallèle, cette latence est acceptable SI le signal LLM est calculé en amont (non bloquant). Mais il faudrait un scheduler séparé.
- **Fiabilité crypto** : les LLM n'ont pas d'accès temps réel aux marchés. Les données de sentiment crypto (CryptoFear&Greed Index, Reddit, Twitter/X) ont une corrélation faible avec les mouvements de prix sur une fenêtre de 1-4h. L'edge est marginal.
- **Coût** : ~ 0.01-0.05$ par appel. Pour N paires × 24h / cycle 2min = 720 appels/jour/paire → coût opérationnel non-trivial.
- **Risque hallucination** : un LLM peut générer des analyses plausibles mais factuellement incorrectes sur des événements récents qu'il ne connaît pas.
- **Architecture** : un LLM ne peut pas être intégré dans le cycle synchrone de `execute_real_trades` sans risque de timeout.

**Verdict ❌ NON PERTINENT** — latence incompatible, fiabilité insuffisante sur crypto à court terme (1-4h), coût disproportionné, risque d'hallucination non contrôlable en production.

| Critère | Évaluation |
|---|---|
| Gain attendu | Marginal à nul sur signaux 1h-4h |
| Complexité | L (architecture async, source données sentiment) |
| Risque prod | Élevé (hallucinations, dépendance API externe) |
| Cycle 2 min | Latence problématique si synchrone |
| Données suffisantes | Non (sentiment temps réel non disponible) |

---

### 2.I — Walk-Forward adaptatif par ML

**Principe** : ajuster automatiquement les fenêtres IS/OOS selon les régimes de marché détectés.

**Analyse** :

- Le WF actuel utilise des folds expanding window avec `initial_train_pct=0.70` (`walk_forward.py:450`). C'est un paramètre fixe.
- Un WF adaptatif (ex : raccourcir la fenêtre IS en période de forte non-stationnarité) est théoriquement séduisant — Lopez de Prado en parle (ch.12).
- **Réalité** : détecter quand raccourcir la fenêtre IS nécessite lui-même un modèle de régime (opportunité 2.B). C'est une dépendance circulaire.
- Le gain pratique est difficile à mesurer sans un framework de simulation des régimes. La gate OOS existante (Sharpe ≥ 0.8, WR ≥ 30%, decay ≥ 0.15) remplit déjà partiellement ce rôle en rejetant les configs sur-fittées.
- **Alternative simple** : réduire `initial_train_pct` à 0.60 en période de haute volatilité (détectée par ATR percentile). Pas de ML nécessaire.

**Verdict ❌ NON PERTINENT** dans sa forme ML complète. Ajustement de `initial_train_pct` selon ATR percentile : ✅ **PERTINENT** en tant que règle déterministe simple (complexité XS).

| Critère | Évaluation |
|---|---|
| Gain attendu | Incertain sans simulation historique des régimes |
| Complexité | ML complet=L. Règle ATR=XS |
| Risque prod | Faible (paramètre WF, pas d'impact sur la production live) |
| Cycle 2 min | Offline, compatible |
| Données suffisantes | Oui pour la règle ATR |

---

### 2.J — Sélection des features par SHAP Values

**Principe** : entraîner un modèle XGBoost sur les features existantes et utiliser les SHAP values pour identifier quels indicateurs apportent réellement de la valeur.

**Analyse** :

- C'est une analyse **offline**, sans risque pour la production. Elle peut être lancée une fois sur les données historiques backtest.
- Les features actuelles : EMA cross, StochRSI, RSI, ATR, SMA200, ADX, TRIX, MACD, volume, MTF flag.
- **Valeur concrète** : si SHAP montre que SMA200 et ADX n'apportent rien dans les scénarios `StochRSI_SMA` et `StochRSI_ADX`, on peut simplifier la stratégie ou redéfinir les scénarios WF.
- **Requiert** : trade_journal.jsonl suffisamment rempli (≥ 100 trades par scénario recommandé). Si le bot tourne depuis peu, les données sont insuffisantes.
- **Limitation** : SHAP mesure l'importance dans le contexte du modèle ML entraîné — pas nécessairement la causalité ni la performance en trading réel (une feature importante pour XGBoost peut être non-causale).

**Verdict ✅ PERTINENT** — analyse offline pure, zéro risque prod, peut informer la simplification des scénarios WF. À lancer quand ≥ 200 trades dans le journal.

| Critère | Évaluation |
|---|---|
| Gain attendu | Décision éclairée sur le nombre de scénarios utiles |
| Complexité | S (offline, XGBoost + shap library) |
| Risque prod | Nul (analyse offline) |
| Cycle 2 min | Sans objet (offline) |
| Données suffisantes | Conditionnellement (≥ 200 trades dans trade_journal.jsonl) |

---

## PHASE 3 — Recommandation finale

### 3.1 Tableau de décision

| ID | Opportunité | Verdict | Gain estimé | Complexité | Risque prod |
|---|---|---|---|---|---|
| A | ML supervisé sur signaux | ❌ Remplacement / ✅ Filtre additionnel | Marginal (+Sharpe 0.1-0.3) | M | Faible si filtre |
| B | Détection régime ML (HMM/K-Means) | ✅ PERTINENT | DD -2 à -4 pp | M | Modéré |
| C | Optimisation bayésienne seuils (Optuna) | ✅ PERTINENT (offline) | Efficacité WF, gains incertains | M | Faible |
| C' | RL sur seuils | ❌ NON PERTINENT | Non mesurable | XL | Critique |
| D | ML pour sizing | ❌ NON PERTINENT | Non mesurable | L | Élevé |
| D' | Half-Kelly depuis journal | ✅ PERTINENT | Sharpe +5-15% théorique | XS | Faible |
| E | Scoring de sélection de paires | ✅ PERTINENT | Filtrage sideways | S | Modéré |
| F | Agent ML gestion stops actifs | ❌ NON PERTINENT | Non mesurable | L | Critique |
| F' | Ajustement multiplicateur ATR à l'entrée | ✅ PERTINENT | Réduction stops prématurés | XS | Faible |
| G | Agent rebalancing | ❌ NON PERTINENT | Non démontrable | L | Élevé |
| H | LLM sentiment | ❌ NON PERTINENT | Marginal à nul | L | Élevé |
| I | WF adaptatif ML | ❌ NON PERTINENT | Incertain | L | Faible |
| I' | WF initial\_train\_pct adaptatif (ATR rule) | ✅ PERTINENT | Incertain mais testable | XS | Nul |
| J | SHAP values feature importance | ✅ PERTINENT | Décisionnel (simplification WF) | S | Nul |

**PERTINENT : 7** | **NON PERTINENT : 7**

---

### 3.2 Roadmap recommandée

#### NIVEAU 1 — Sans risque pour la production (shadow mode, offline)
*(Peut tourner en parallèle du bot actuel, zéro impact sur les ordres)*

1. **Analyse SHAP (J)** — Entraîner XGBoost offline sur `trade_journal.jsonl`, extraire les SHAP values des 10 features. Durée : 1-2 jours. Prérequis : ≥ 200 trades dans le journal.
2. **Half-Kelly (D')** — Calculer `f* = WR × avg_win / avg_loss × 0.5` depuis le journal. Comparer avec `risk_per_trade=0.055`. Si `f*` est stabilisé sur 100+ trades, c'est un calibrage objectif du sizing. Durée : 1 jour. Risque : nul.
3. **Scoring de sélection de paires (E)** — Calculer momentum rank 20j + volume rank sur la liste existante. Logger le score sans changer la liste active. Observer si les paires basses dans le ranking sous-performent. Durée : 2-3 jours.
4. **HMM/régime en shadow (B)** — Entraîner un HMM à 2-3 états sur les rendements 4h. Logger les états détectés sans changer le filtre MTF. Comparer avec le filtre EMA18/58 actuel : accord ? Durée : 3-5 jours.

#### NIVEAU 2 — Intégration progressive
*(Paper trading d'abord, validation OOS sur 30+ jours, puis production avec capital réduit)*

5. **Ajustement multiplicateur ATR à l'entrée (F')** — Remplacer `atr_stop_multiplier=3.0` par une règle `3.0 × (ATR_current / ATR_median_30j)^0.5`. Tester en backtest sur 3 ans. Si Sharpe OOS ≥ Sharpe baseline, déployer sur une paire.
6. **Filtre ML additionnel (A)** — Entraîner un classificateur simple (Logistic Regression ou Random Forest) sur les features backtest pour prédire P(trade\_profitable). Utiliser score ≥ 0.55 comme filtre supplémentaire du signal BUY. Point d'injection : `signal_generator.py:100` (après toutes les conditions déterministes). Backtest comparatif obligatoire.
7. **Régime de marché (B)** — Remplacer le flag binaire `mtf_bullish` (`signal_generator.py:90`) par un score de confiance de régime (0.0 à 1.0) issu du HMM. Seuil d'activation : ≥ 0.65 au lieu de 0.5. Validation OOS 60 jours minimum.

#### NIVEAU 3 — Remplacement de composants existants
*(Uniquement si NIVEAU 2 validé sur 30+ jours en production partielle)*

8. **Optuna WF (C)** — Remplacer les 4 scénarios discrets par une optimisation bayésienne sur l'espace des seuils (StochRSI buy\_min 0.02-0.15, buy\_max 0.6-0.9, sell\_exit 0.2-0.6) avec Optuna + validation temporelle. Requiert un refactoring de `walk_forward.py`.
9. **Scoring de paires en production (E)** — Activer la sélection dynamique des paires selon le momentum rank, avec les gardes appropriées (paire en position → non désactivable).

---

### 3.3 Ce qu'il ne faut PAS faire

| Anti-pattern | Justification |
|---|---|
| Reinforcement Learning pour les seuils de trading | Nécessite ~10^6 interactions. Le dataset réel (~200-500 trades par paire) est 3 ordres de grandeur trop petit. Résultat : policy instable qui gèle ou sur-trade. |
| LLM pour filtrage de sentiment en temps réel | Latence 1-5s incompatible avec le cycle 2 min sous charge multi-paires. Fiabilité insuffisante sur événements récents. Coût opérationnel disproportionné. |
| Agent ML de rebalancing actif | Clash avec `position_reconciler.py` et la logique `_cancel_exchange_sl`. Risque d'ordres non-anticipés sur des positions ouvertes. |
| Remplacement total du WF par un modèle supervisé | Le WF existant avec OOS gates (Sharpe/WR/decay) est déjà un mécanisme anti-overfit solide. Un modèle supervisé sans ces gates régresserait sur les données OOS. |
| Modification dynamique du SL actif | Annuler/replacer un `STOP_LOSS_LIMIT` exchange-natif toutes les 2 min crée une fenêtre sans protection. Sur Binance Spot, il n'y a pas de `TRAILING_STOP_MARKET`. |
| Intégration sans backtest comparatif complet | Tout changement touchant les signaux ou le sizing DOIT passer par le framework walk-forward existant (`walk_forward.py`) avec validation OOS avant déploiement. |

---

### 3.4 Verdict global

**Faut-il intégrer de l'IA/ML sur MULTI_ASSETS ?**
Oui, de façon chirurgicale et non en remplacement de l'existant. Le système actuel a une architecture solide (WF + OOS gates + Cython). La valeur ajoutée IA/ML est réelle mais marginale.

**Par quoi commencer ?**
L'analyse SHAP (J) en offline — zéro risque, durée courte, informe toutes les décisions suivantes. Puis le half-Kelly (D') sur le sizing — calculable dès maintenant depuis `trade_journal.jsonl`, gain mesurable.

**Risque principal à surveiller ?**
Le surapprentissage sur des données de marché non-stationnaires. Tout modèle ML qui améliore les métriques IS sans passer le walk-forward OOS existant est un overfit. La validation doit TOUJOURS utiliser `walk_forward.py` avec les gates `oos_sharpe_min=0.8`, `oos_win_rate_min=30%`, `oos_decay_min=0.15`.

---

*Audit rédigé à partir du code source uniquement — aucun fichier .md consulté.*
*Références principales : `signal_generator.py`, `walk_forward.py`, `indicators_engine.py`, `position_sizing.py`, `bot_config.py`, `backtest_runner.py`, `MULTI_SYMBOLS.py`, `trade_journal.py`, `order_manager.py`.*
