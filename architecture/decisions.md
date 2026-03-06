# Architecture Decision Records — MULTI_ASSETS

> Format : ADR (Architecture Decision Record) — chaque décision documente le contexte, les alternatives évaluées, le choix retenu et ses conséquences.

---

## ADR-001 — État persisté en JSON + HMAC-SHA256

**Statut** : Accepté  
**Date** : 2024

### Contexte
Le bot doit survivre aux redémarrages (PM2, watchdog, crash OS) sans perdre l'état des positions ouvertes ni les `sl_order_id`. Un état corrompu peut entraîner des achats en double ou un stop-loss fantôme.

### Alternatives évaluées
| Option | Évalué | Rejeté car |
|--------|--------|-----------|
| SQLite | Oui | Verrouillage fichier incompatible avec PM2 multi-process |
| Redis | Oui | Dépendance externe, complexité opérationnelle sur Windows |
| Pickle Python | Oui | Pas de détection de corruption, vulnérable à l'injection |
| JSON brut | Oui | Aucune protection contre la modification manuelle silencieuse |
| **JSON + HMAC-SHA256** | **Retenu** | Détection garantie de toute corruption ou édition manuelle |

### Décision
Format `JSON_V1:<payload_b64>:<hmac_hex>`. Clé HMAC = `BINANCE_SECRET_KEY`. Toute mismatch lève `StateError` → démarrage avec état vide + réconciliation API.

### Conséquences
- (+) Intégrité garantie à chaque chargement
- (+) Lisible par un humain (JSON base64 décodable)
- (-) `BINANCE_SECRET_KEY` requise au démarrage, même sans activité de trading
- (-) Toute modification manuelle de `bot_state.json` invalide le HMAC

---

## ADR-002 — Fees backtest figés, jamais synchronisés avec les fees live

**Statut** : Accepté  
**Date** : 2024

### Contexte
Les frais Binance varient selon le niveau VIP et les promotions. Synchroniser les fees backtest avec les fees live introduirait un biais de survie et invaliderait les comparaisons historiques entre scénarios.

### Alternatives évaluées
| Option | Évalué | Rejeté car |
|--------|--------|-----------|
| Fees dynamiques (API `/sapi/v1/asset/tradeFee`) | Oui | Variance entre sessions → résultats non-reproductibles |
| Fees live injectés dans backtest | Oui | Biais de survie si fees baissent au fil du temps |
| **Fees figés dans `bot_config.py`** | **Retenu** | Reproductibilité totale, comparaison objective entre scénarios |

### Décision
`backtest_taker_fee = 0.0007`, `backtest_maker_fee = 0.0002` sont des constantes dans `bot_config.py`. Elles ne doivent **jamais** être écrasées par `taker_fee`/`maker_fee` live. Modification uniquement dans `backtest_runner.py`.

### Conséquences
- (+) Backtests reproductibles à n'importe quelle date
- (+) Comparaison inter-scénarios valide
- (-) Si les fees réels changent significativement, les backtests surestiment/sous-estiment les coûts

---

## ADR-003 — Stop-loss via STOP_LOSS_LIMIT exchange-natif (pas de trailing manuel)

**Statut** : Accepté  
**Date** : 2024

### Contexte
Binance Spot ne supporte pas `TRAILING_STOP_MARKET` (disponible uniquement sur Futures). Un stop-loss logiciel (vérifié à chaque bougie) expose au slippage et aux gaps de prix nocturnes.

### Alternatives évaluées
| Option | Évalué | Rejeté car |
|--------|--------|-----------|
| `TRAILING_STOP_MARKET` | Oui | **N'existe pas sur Spot** → `NotImplementedError` |
| Stop-loss logiciel (check toutes les heures) | Oui | Exposition au slippage nocturne, dépend de la disponibilité du bot |
| Stop-loss manuel via websocket prix | Oui | Complexité élevée, autre point de failure |
| **`STOP_LOSS_LIMIT` exchange-natif** | **Retenu** | Exécution garantie même si le bot est hors ligne |

### Décision
Après chaque BUY, un ordre `STOP_LOSS_LIMIT` est posé immédiatement sur l'exchange. Si le placement échoue → `safe_market_sell` d'urgence. `sl_order_id` et `sl_exchange_placed` persistés dans `pair_state`.

### Conséquences
- (+) Protection capital garantie même en cas de crash du bot
- (+) Exécution à prix connu (LIMIT, pas MARKET)
- (-) Le prix LIMIT peut ne pas être rempli en cas de gap violent (flash crash)
- (-) Nécessite un suivi de `sl_order_id` dans l'état persisté

---

## ADR-004 — Rate limiter Token Bucket (18 req/s)

**Statut** : Accepté  
**Date** : 2024

### Contexte
Binance impose une limite de 1200 requêtes/minute (poids variable). Dépasser cette limite entraîne un ban IP temporaire (HTTP 429) voire permanent (HTTP 418).

### Alternatives évaluées
| Option | Évalué | Rejeté car |
|--------|--------|-----------|
| Pas de rate limiting (raw) | Oui | Ban IP quasi-certain en production multi-paires |
| `time.sleep` fixe entre requêtes | Oui | Trop conservateur, latence inutile |
| Leaky bucket | Oui | Moins adapté aux bursts légitimes (ex: initialisation) |
| **Token bucket (18 req/s)** | **Retenu** | Permet les bursts courts tout en respectant la limite sur sliding window |

### Décision
`_TokenBucket(rate=18.0, capacity=18.0)` dans `BinanceFinalClient`. Toutes les requêtes API appellent `_rate_limiter.acquire()` avant exécution. 18 req/s = 1080 req/min, marge de 10% sous la limite Binance.

### Conséquences
- (+) Protection contre le ban IP
- (+) Permet les bursts jusqu'à 18 requêtes simultanées
- (-) Ajoute de la latence en période de charge élevée
- (-) Capacité bucket (`capacity=18`) à revoir si Binance augmente les poids

---

## ADR-005 — Optimisation Cython opt-in (backtest_engine_standard, indicators)

**Statut** : Accepté  
**Date** : 2024

### Contexte
Le backtest sur 1095 jours × 4 scénarios × N paires peut prendre plusieurs minutes en Python pur, rendant le walk-forward impraticable en temps réel.

### Alternatives évaluées
| Option | Évalué | Rejeté car |
|--------|--------|-----------|
| Python pur | Oui | Trop lent pour WF en production |
| Numba JIT | Oui | Compatibilité Pandas 3.0 fragile, overhead warm-up |
| Rust (via PyO3) | Oui | Complexité de build, chaîne CI non établie |
| **Cython `.pyd` compilés** | **Retenu** | Performance ×10-50, intégration Python native, build CI reproductible |

### Décision
`code/bin/backtest_engine_standard.pyd` et `indicators.pyd` sont des modules Cython pré-compilés. Le code Python charge d'abord le `.pyd`, avec fallback sur Python pur si absent. Sources `.pyx` versionées dans `code/`.

### Conséquences
- (+) Backtest ×10-50 plus rapide
- (+) Fallback Python pur si les `.pyd` ne sont pas compilés
- (-) Build nécessite `setup.py` + MSVC/GCC sur Windows
- (-) Les `.pyd` doivent être recompilés à chaque mise à jour Python mineure

---

## ADR-006 — Concurrence via RLock global + locks par paire

**Statut** : Accepté  
**Date** : 2024

### Contexte
Le scheduler exécute plusieurs paires en parallèle (threads). Deux threads qui lisent/écrivent `bot_state` simultanément peuvent créer des races conditions sur le solde USDC ou l'état d'une position.

### Alternatives évaluées
| Option | Évalué | Rejeté car |
|--------|--------|-----------|
| GIL Python seul | Oui | Le GIL ne protège pas les opérations composées (read-modify-write) |
| `asyncio` (event loop) | Oui | Bibliothèque Binance (`python-binance`) non-native async, refactoring massif |
| Lock global unique | Oui | Contention élevée, sérialise toutes les paires |
| **RLock global + locks par paire** | **Retenu** | Granularité optimale : global pour `bot_state`, paire pour l'exécution |

### Décision
- `_bot_state_lock` (RLock) : protège **toutes** les lectures/écritures de `bot_state`
- `_pair_execution_locks[pair]` : empêche l'exécution concurrente de la même paire
- `_oos_alert_lock` : protège `_oos_alert_last_sent` (dédoublonnage alertes email)

### Conséquences
- (+) Thread-safety garantie sans sérialiser toutes les paires
- (+) Compatible avec le scheduler synchrone existant
- (-) Risque de deadlock si un lock est acquis dans le mauvais ordre (documenté dans `code_auditor.md`)
- (-) `save_bot_state()` throttlé à 5s pour réduire la contention

---

## ADR-007 — Walk-Forward avec OOS gates (Sharpe ≥ 0.8, WinRate ≥ 30%, decay ≥ 0.15)

**Statut** : Accepté  
**Date** : 2024

### Contexte
Un modèle qui performe bien en backtest mais mal en production indique un overfitting. Les OOS gates bloquent le déploiement des scénarios qui ne généralisent pas.

### Alternatives évaluées
| Option | Évalué | Rejeté car |
|--------|--------|-----------|
| Backtest simple (pas de WF) | Oui | Overfitting non détecté |
| Cross-validation temporelle (expanding) | Oui | Impraticable en temps réel |
| Walk-forward anchré simple (1 split) | Oui | Variance élevée sur 1 split |
| **Walk-forward anchré multi-split + OOS gates** | **Retenu** | Détection robuste de la degradation OOS, référence Lopez de Prado |

### Décision
Fenêtre backtest = 1095 jours glissants (calculé dynamiquement via `_fresh_start_date()`). OOS gates : `sharpe_ratio >= 0.8` ET `win_rate >= 0.30` ET `performance_decay >= 0.15`. `oos_blocked=True` dans `pair_state` jusqu'à validation.

### Conséquences
- (+) Réduit significativement le risque de déployer un modèle overfitté
- (+) `oos_blocked` persisté → survie aux redémarrages
- (-) Les gates peuvent bloquer des stratégies valides sur des marchés baissiers prolongés
- (-) Seuils empiriques — à recalibrer si le régime de marché change durablement
