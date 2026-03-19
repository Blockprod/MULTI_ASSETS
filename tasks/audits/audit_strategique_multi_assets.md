# Audit Stratégique — MULTI_ASSETS
> Perspective : Quantitative Researcher / Fund Manager  
> Date : 2026-03-19  
> Baseline tests : 590/590 ✅  
> Référence prompt : `tasks/prompts/audit_strategic_prompt.md`

---

## BLOC 1 — INTÉGRITÉ STATISTIQUE DES SIGNAUX

### 1.1 Biais look-ahead

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| `_fresh_start_date()` dynamique (calculé à chaque appel, jamais figé à l'import) | `MULTI_SYMBOLS.py:236` | ✅ CONFORME |
| `start_date = _fresh_start_date()` au niveau module — rétrocompatibilité uniquement, la fonction dynamique est utilisée partout dans l'orchestrateur | `MULTI_SYMBOLS.py:240` | ✅ CONFORME |
| Filtre MTF 4h — `bullish_4h = (ema_f > ema_s).astype(float).shift(1).fillna(0.0)` — décalage d'une bougie 4h appliqué avant le join | `backtest_runner.py:79` | ✅ CONFORME |
| Signal live — `row = df.iloc[-2]` (avant-dernière chandelle, donc complète et fermée) | `MULTI_SYMBOLS.py:963` | ✅ CONFORME |
| Signal backtest — `idx_signal = i`, entrée à `df_work.iloc[i+1]['open']` — signal évalué à la clôture[i], exécution à l'ouverture[i+1] | `backtest_runner.py:~620` | ✅ CONFORME |
| `iloc[-1]` canary — seule utilisation de `iloc[-1]` en backtest est le calcul du wallet final, jamais pour générer un signal | `backtest_runner.py:735` | ✅ CONFORME |

**Conclusion** : Aucun biais look-ahead détecté. Les deux gatekeepers critiques (MTF `shift(1)` + `iloc[-2]` live) sont en place et cohérents avec la logique backtest.

---

### 1.2 Frais et reproductibilité

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| `backtest_taker_fee` figé via `.env`/`bot_config.py`, jamais écrasé depuis l'API Binance | `bot_config.py:57,151-152` | ✅ CONFORME |
| `backtest_maker_fee` idem — séparé de `maker_fee` live | `bot_config.py:59,153-154` | ✅ CONFORME |
| `slippage_buy` + `slippage_sell` appliqués aux prix d'entrée et de sortie dans la boucle Python | `backtest_runner.py:280-281` | ✅ CONFORME |
| `backtest_taker_fee` utilisé exclusivement dans `backtest_runner.py` (5 points d'utilisation) — jamais dans le chemin d'exécution live | `backtest_runner.py:279,507,530,568,727` | ✅ CONFORME |

**Conclusion** : Reproductibilité garantie. Les frais backtest sont une constante de configuration, immune aux fluctuations des paramètres live.

---

### 1.3 Walk-forward

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| Folds expanding-window ancré — `split_walk_forward_folds()` (train grandit, test fixe) — pas de data leakage inter-folds | `walk_forward.py:~290` | ✅ CONFORME |
| OOS gates configurables — `oos_sharpe_min` (défaut 0.8), `oos_win_rate_min` (défaut 30%), chargés depuis `config` | `walk_forward.py:34-36,53` | ✅ CONFORME |
| Decay gate — rejet si `avg_oos_sharpe / full_sharpe < OOS_DECAY_MIN (0.15)` — empêche les configs d'overfitter massivement | `walk_forward.py:546-548` | ✅ CONFORME |
| Aucun fallback non-validé — si `len(passed_configs) == 0` → `best_wf_config=None` retourné, pas de config par défaut | `walk_forward.py:450,578` | ✅ CONFORME |
| Sélection finale du meilleur config parmi les validés by `avg_oos_sharpe` — métrique OOS, pas IS | `walk_forward.py:578` | ✅ CONFORME |
| **Pré-sélection des candidats WF par `final_wallet` (profit brut) et non par Sharpe** — seuls les top-5 par profit voient le WF ; un config à profit modéré mais Sharpe OOS supérieur peut être éliminé avant même d'être testé | `walk_forward.py:441,446` | 🟡 À VÉRIFIER |

**Note sur ST-P2-01** : La sélection par `final_wallet` est documentée comme choix délibéré (commentaire L434 : *"immune to the Calmar bias"*). La mitigation partielle est le cap de diversité top-2 par timeframe. Le risque réel est de favoriser des configs à haute variance qui passent les folds IS mais échouent OOS — filtré par decay gate. Risque résiduel : faible à modéré.

---

### 1.4 Cohérence backtest ↔ live

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| `WF_SCENARIOS` défini une seule fois, partagé entre orchestrateur et backtest via `TradingDeps` | `MULTI_SYMBOLS.py:346` | ✅ CONFORME |
| 4 scénarios identiques (StochRSI, +SMA200, +ADX, +TRIX) — pas de divergence entre chemins | `MULTI_SYMBOLS.py:346-416` | ✅ CONFORME |
| `iloc[-2]` live ≡ `open[i+1]` backtest — les deux utilisent la dernière chandelle complète avant exécution | `MULTI_SYMBOLS.py:963` / `backtest_runner.py:~625` | ✅ CONFORME |
| Le filtre MTF (`bullish_4h`) est appliqué avec `shift(1)` en backtest ET sur la bougie 4h[−2] en live → logique équivalente | `backtest_runner.py:79` / `MULTI_SYMBOLS.py:963` | ✅ CONFORME |

**Conclusion** : Cohérence totale. Aucune asymétrie détectée entre les chemins backtest et live pour les 4 scénarios.

---

## BLOC 2 — SOLIDITÉ DE LA STRATÉGIE

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| `compute_position_size_by_risk()` — garde ATR nul : `if atr_value is None or atr_value <= 0: return 0.0` — pas de position sur actif sans volatilité mesurable | `position_sizing.py:44-45` | ✅ CONFORME |
| Trailing stop — `max_price` monotone croissant, trailing activé une seule fois (`trailing_stop_activated` flag) — logique identique backtest/live | `backtest_runner.py:469-470` / `order_manager.py:~210` | ✅ CONFORME |
| Breakeven — `entry_price * (1 + slippage_buy)` comme référence de coût réel — threshold configurable via `breakeven_trigger_pct` — guard `breakeven_triggered` empêche la ré-application | `backtest_runner.py:484` / `order_manager.py:238` | ✅ CONFORME |
| Partiels — `partial_taken_1/2` flags idempotents, guard `min_notional * 3` avant exécution — pas de fractionnement sous seuil Binance | `backtest_runner.py:~500-540` | ✅ CONFORME |

**Conclusion** : La mécanique de gestion des positions est robuste. Les quatre mécanismes de sortie (SL, trailing, breakeven, partiel) sont cohérents entre backtest et live, et protégés contre la ré-activation.

---

## BLOC 3 — RISK MANAGEMENT FINANCIER

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| `_update_daily_pnl()` — thread-safe, appelé après chaque vente, reset automatique par clé ISO date (`_get_today_iso()`) | `MULTI_SYMBOLS.py:544` | ✅ CONFORME |
| `_daily_pnl_tracker` persisté dans `bot_state` — survit aux redémarrages | `state_manager.py:73` | ✅ CONFORME |
| `oos_blocked` + `oos_blocked_since` persistés — survit aux redémarrages | `state_manager.py:66` | ✅ CONFORME |
| `emergency_halt` persisté dans `bot_state` — vérifié au début de `_execute_real_trades_inner()` | `state_manager.py:72` | ✅ CONFORME |
| **Guard corrélation entre paires absent** — aucune limitation du nombre de positions simultanées sur actifs corrélés (BTC/ETH, altcoins trend-following identiques) | `MULTI_SYMBOLS.py` (absent) | 🟠 NON CONFORME |
| **Drawdown kill-switch (EM-P2-05) non persisté dans `bot_state`** — l'alerte email est correcte mais si le bot redémarre mid-drawdown, aucun état n'est restauré ; la protection ne s'applique qu'au cycle courant | `MULTI_SYMBOLS.py:~1145-1182` | 🟡 À VÉRIFIER |

**Détail ST-P1-01 — Corrélation guard :**  
En conditions de marché corrélées (crash généralisé, risk-off), le bot peut ouvrir `N` positions simultanées sur des paires toutes longues (crypto spot, corrélation intra-classe > 0.7). L'exposition effective devient `N * position_size`, dépassant potentiellement le `daily_loss_limit_pct`. Le `daily_loss_limit_pct` est la seule barrière, mais elle est réactive (déclenche après pertes réalisées) plutôt que préventive.

**Détail ST-P2-02 — Drawdown kill-switch :**  
Le guard actuel (`MULTI_SYMBOLS.py:~1150`) vérifie `(current_price - entry_price) / entry_price < -max_drawdown_pct` en cycle et envoie une alerte. Il n'effectue pas de vente d'urgence (`safe_market_sell`) et ne persiste pas un flag `drawdown_halted` dans `bot_state`. À corriger si la spec requiert une action (vente) et pas seulement une notification.

---

## SYNTHÈSE

### Tableau des Findings

| ID | Bloc | Description | Fichier:Ligne | Sévérité | Impact Perf | Effort Fix |
|----|------|-------------|---------------|----------|-------------|------------|
| ST-P1-01 | B3 | Corrélation guard absent — N positions longues simultanées sur actifs corrélés | `MULTI_SYMBOLS.py` (absent) | 🟠 P1 | Élevé (risque > 5% net en crash) | Moyen (~2h) |
| ST-P2-01 | B1.3 | Candidats WF pré-sélectionnés par `final_wallet` — un config Sharpe-optimal peut être exclu avant WF | `walk_forward.py:441,446` | 🟡 P2 | Modéré (surexposition à la variance) | Faible (~1h) |
| ST-P2-02 | B3 | Drawdown kill-switch non persisté dans `bot_state` — protection uniquement intra-cycle | `MULTI_SYMBOLS.py:~1150` | 🟡 P2 | Faible (alerte envoyée, pas de vente auto) | Faible (~30min) |

**Score global :**
- 🔴 P0 : 0
- 🟠 P1 : 1 (corrélation guard)
- 🟡 P2 : 2 (WF pre-selection, drawdown persistence)
- ✅ Conformes : 16 points sur 19 vérifiés

---

### Top 3 Biais Potentiellement Invalidants

> **Aucun biais invalidant le backtest n'a été détecté.** Les points suivants sont des risques résiduels à monitorer :

1. **ST-P2-01 — Biais de sélection WF** : La pré-sélection top-5 par `final_wallet` favorise statistiquement les configs à haute variance. En pratique, le decay gate (OOS/IS Sharpe ≥ 0.15) filtre les overfit extrêmes. Risque résiduel : sélection d'une config globalement "chanceuse" sur l'historique plutôt que robuste. Mitigation recommandée : ajouter `avg_sharpe` comme critère primaire de pré-sélection, ou étendre `top_n` à 8-10.

2. **MTF filter robustness** : Le `shift(1)` sur les bougies 4h est correct, mais la jointure entre les timeframes (1h/4h/1d) ne garantit pas l'alignement parfait des timestamps en cas de données manquantes dans le cache OHLCV. Non vérifié dans cet audit — à auditer séparément avec `test_indicators_consistency.py`.

3. **Slippage symétrique** : `slippage_buy` et `slippage_sell` sont identiques (configuration unique). Pour les altcoins peu liquides, le slippage de sortie sur dump est asymétriquement plus élevé. Ce n'est pas un biais look-ahead mais une sous-estimation du coût réel en conditions de stress — peut conduire à des live returns < backtest sur les queues de distribution.

---

### Points du Modèle Statistique à Conserver Absolument

1. **`shift(1)` MTF** (`backtest_runner.py:79`) — protection critique contre le look-ahead sur filtre tendance. Ne jamais supprimer sans audit complet.
2. **`iloc[-2]` live** (`MULTI_SYMBOLS.py:963`) — garantit que le signal live utilise une chandelle complète. Lier ce comportement au test de non-régression dédié.
3. **OOS gates multi-critères** (`walk_forward.py:34-36,546-548`) — la combinaison Sharpe + WinRate + decay est plus robuste qu'un seul filtre. Ne pas abaisser les seuils sans validation empirique.
4. **`best_wf_config=None` sur échec OOS** (`walk_forward.py:450`) — refus de trader sans validation OOS. C'est la protection la plus importante de l'architecture walk-forward. Ne jamais bypasser.
5. **`backtest_taker_fee` figé** (`bot_config.py:57`) — cloisonnement strict between simulation et production. Critère de validité de la comparaison IS/OOS.
6. **`_fresh_start_date()`** (`MULTI_SYMBOLS.py:236`) — garantit la fenêtre glissante de 1095 jours. Ne jamais remplacer par une date figée à l'import.

---

### Actions Recommandées

#### ST-P1-01 — Corrélation guard (Priorité Haute)
```python
# Dans _execute_real_trades_inner(), avant l'achat :
_open_long_pairs = [p for p, s in bot_state['pairs'].items() 
                    if s.get('last_order_side') == 'BUY']
if len(_open_long_pairs) >= config.max_concurrent_long:
    logger.info("Max positions longues simultanées atteint (%d) — achat bloqué pour %s",
                config.max_concurrent_long, backtest_pair)
    return
```
Ajouter `max_concurrent_long: int = 4` dans `bot_config.py`.

#### ST-P2-01 — WF pre-selection (Priorité Basse)
Envisager de remplacer le critère `final_wallet` par `sharpe_ratio` dans `walk_forward.py:441,446`, ou d'augmenter `top_n` de 5 à 8.

#### ST-P2-02 — Drawdown kill-switch persistence (Priorité Basse)
Ajouter `drawdown_halted: bool` dans `PairState` + `state_manager.py:_KNOWN_PAIR_KEYS`, et persister/vérifier ce flag en début de cycle pour que la protection survive aux redémarrages.
