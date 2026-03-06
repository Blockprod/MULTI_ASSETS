# Plan de File Engineering — MULTI_ASSETS

*Basé sur l'analyse complète du code source (Mars 2026)*

---

## 1. Fichiers de contexte IA

### 1.1 `.github/copilot-instructions.md`
**Impact : maximal** — injecté automatiquement à chaque session Copilot Chat.

**Justification des choix de contenu** : chaque règle ci-dessous correspond à un bug ou une confusion déjà rencontrée dans l'historique d'audit (TRAILING_STOP_MARKET sur Spot, backtest_fees mutées, `except: pass`, start_date figée).

```markdown
# MULTI_ASSETS — Copilot Instructions

## Stack
- **Python 3.13** · Binance **Spot uniquement** (pas Futures) · Pandas 3.0 / NumPy 2.4
- Quote currency: **USDC** (jamais USDT) sur toutes les paires
- Venv: `.venv/` · Tests: `pytest` depuis `c:\Users\averr\MULTI_ASSETS`
- PM2 + `code/src/watchdog.py` assurent la continuité de service

## Structure
```
code/src/      ← tous les modules Python
code/bin/      ← .pyd Cython compilés (backtest_engine_standard, indicators)
states/        ← bot_state.json (JSON_V1 + HMAC-SHA256)
cache/         ← cache OHLCV pickle (TTL 30 jours)
tests/         ← pytest (33+ tests)
config/        ← ecosystem.config.js (PM2)
```

## Config & Credentials
- Singleton `Config` dans `bot_config.py`, chargé via `Config.from_env()`
- `Config.__repr__` **masque** api_key/secret_key — ne jamais logger `config` brut
- Vars d'env obligatoires : BINANCE_API_KEY, BINANCE_SECRET_KEY, SENDER_EMAIL,
  RECEIVER_EMAIL, GOOGLE_MAIL_PASSWORD

## Thread Safety — RÈGLE ABSOLUE
- `_bot_state_lock` (RLock) protège TOUTES les écritures dans `bot_state`
- `_pair_execution_locks[pair]` empêche l'exécution concurrente par paire
- `_oos_alert_lock` protège `_oos_alert_last_sent`
- `save_bot_state()` : throttlé 5s, utiliser `force=True` pour les saves critiques

## Stop-Loss — Règles critiques
- `TRAILING_STOP_MARKET` **n'existe pas sur Spot** → `NotImplementedError`
- Stop-loss = `STOP_LOSS_LIMIT` exchange-natif (pas manuel)
- Après chaque BUY, le SL **doit** être posé immédiatement → sinon `safe_market_sell` d'urgence
- `sl_order_id` et `sl_exchange_placed` persistés dans `pair_state`
- `recvWindow = 60000ms` (centralisé dans `config.recv_window`)

## Fees
- Live : `taker_fee=0.0007`, `maker_fee=0.0002`
- `backtest_taker_fee` / `backtest_maker_fee` sont **FIGÉS** et ne doivent JAMAIS
  être écrasées par les fees live. Modifier backtest_runner.py uniquement.

## État persisté
- Format `JSON_V1:` + HMAC-SHA256 (clé = `BINANCE_SECRET_KEY`)
- `PairState` TypedDict dans `MULTI_SYMBOLS.py` ; `_KNOWN_PAIR_KEYS` dans `state_manager.py`
- `StateError` sur HMAC mismatch → démarrage avec état vide + réconciliation API

## Protection du capital
- `daily_loss_limit_pct=0.05` (5% de 10 000 USDC) bloque les achats si dépassé
- 3 échecs consécutifs `save_bot_state()` → `emergency_halt = True`
- `oos_blocked=True` dans pair_state bloque les achats jusqu'à validation OOS
- `OOS gates` : Sharpe ≥ 0.8, WinRate ≥ 30%, decay ≥ 0.15

## Signaux & Backtest
- `WF_SCENARIOS` (MULTI_SYMBOLS.py) : 4 scénarios — StochRSI, +SMA200, +ADX, +TRIX
- Fenêtre backtest : 1095 jours glissants (`_fresh_start_date()` — jamais une variable figée)
- MTF filter 4h : EMA18 > EMA58, avec `shift(1)` sur les bougies 4h (anti look-ahead)
- Rate limiter : token bucket 18 req/s dans `exchange_client.py`
- Idempotence : check `origClientOrderId` avant chaque retry dans `safe_market_buy/sell`

## Validation systématique après chaque modification
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/<fichier>.py').read()); print('OK')"
pytest tests/ -x -q
```

## Interdictions absolues
- `except Exception: pass` ou `except Exception: ...` muet → toujours `logger.debug/warning/error`
- Logger `config.api_key` ou `config.secret_key` en clair
- Utiliser une `start_date` figée à l'import → utiliser `_fresh_start_date()`
- Modifier `backtest_taker_fee` au runtime
- Appeler `TRAILING_STOP_MARKET` sur Spot
```

---

### 1.2 `.claude/context.md`
**Rôle** : contexte de fond pour Claude — lu explicitement en début de session complexe.

```markdown
# MULTI_ASSETS — Contexte projet pour Claude

## Qu'est-ce que ce projet ?
Bot de trading algorithmique multi-paires sur **Binance Spot** (USDC comme quote).
Architecture : backtest walk-forward adaptatif → sélection des meilleurs paramètres →
exécution live toutes les 2 minutes par paire, en parallèle.

## Pipeline complet d'une décision de trading
```
1. fetch_historical_data()        ← API Binance ou cache pickle (TTL 30j)
2. prepare_base_dataframe()       ← OHLCV + validation intégrité
3. calculate_indicators()         ← EMA, StochRSI, ADX, TRIX (Cython ou Python)
4. run_all_backtests()            ← 4 scénarios × N paramètres × walk-forward
   └─ walk_forward.py             ← OOS gates : Sharpe≥0.8, WinRate≥30%, decay≥0.15
5. select_best_by_calmar()        ← meilleur scénario (ratio Calmar)
6. generate_buy/sell_checker()    ← closures pures depuis signal_generator.py
7. monitor_and_trade_for_pair()   ← décision finale + sizing
   ├─ compute_position_size_*()   ← 3 modes: risk (défaut), fixed, volatility_parity
   ├─ safe_market_buy()           ← idempotence via origClientOrderId
   ├─ place_exchange_stop_loss()  ← STOP_LOSS_LIMIT natif exchange
   └─ log_trade()                 ← journal JSONL append-only
8. schedule (2 min)               ← boucle principale via `schedule` library
```

## Modules et responsabilités
| Module | Responsabilité |
|--------|---------------|
| `MULTI_SYMBOLS.py` | Orchestrateur principal (~3400 lignes) |
| `bot_config.py` | Config singleton + décorateurs |
| `exchange_client.py` | Client Binance robuste + rate limiter |
| `state_manager.py` | Persistance JSON+HMAC |
| `signal_generator.py` | Closures buy/sell pures |
| `backtest_runner.py` | Moteur backtest (Cython first) |
| `indicators_engine.py` | Indicateurs TA (Cython first) |
| `walk_forward.py` | Métriques OOS + gates anti-overfit |
| `position_sizing.py` | 3 modes de sizing |
| `data_fetcher.py` | Fetch + validation données |
| `cache_manager.py` | Cache disque pickle (TTL 30j) |
| `trade_journal.py` | Journal JSONL thread-safe |
| `watchdog.py` | Surveillance processus + heartbeat |
| `exceptions.py` | Hiérarchie d'exceptions typée |

## Contraintes exchange critiques
- **Binance Spot uniquement** : pas de Futures, pas de leverage, pas de TRAILING_STOP_MARKET
- Quote : **USDC** (pas USDT)
- Filtres sur chaque paire : `LOT_SIZE` (stepSize), `MIN_NOTIONAL` (min 5 USDC en backtest)
- Rate limite Binance Spot : 1200 req/min → token bucket à 18 req/s (marge de 10%)
- `recvWindow = 60 000 ms` pour absorber les dérives horloge
- Timestamp offset calculé précisément : `offset = serverTime - localTime - latence/2 - 500ms`

## État global (`bot_state`)
```python
bot_state = {
    'emergency_halt': bool,          # kill-switch global
    'emergency_halt_reason': str,
    '_daily_pnl_tracker': {          # tracker perte journalière
        'YYYY-MM-DD': {'total_pnl': float, 'trade_count': int}
    },
    'BTCUSDC': PairState,            # une clé par paire active
    'SOLUSDC': PairState,
    # ...
}
```

## Optimisations déjà validées (bench)
| Paramètre | Avant | Après optimisé | Impact |
|-----------|-------|---------------|--------|
| `atr_multiplier` | 5.5 | 8.0 | PnL +22.5%, DD -0.9pp |
| `risk_per_trade` | 5.0% | 5.5% | Calmar max 2.004 |
| `stoch_rsi_sell_exit` | 0.2 | 0.4 | PnL +2%, DD -1pp |
| MTF filter 4h | désactivé | activé | benchmark positif |
| `breakeven_trigger_pct` | - | 2% | benchmark optimal |
| `stop_loss_cooldown` | - | 12 candles | benchmark optimal |

## Ce qui NE doit PAS changer sans benchmark
- `backtest_taker_fee` (0.0007) et `backtest_maker_fee` (0.0002) : figés pour reproductibilité
- `WF_SCENARIOS` : les 4 scénarios sont calibrés pour le crypto trend-following
- `OOS_DECAY_MIN = 0.15` : seuil anti-overfit (ratio OOS/FS Sharpe)
- Séquence de protection : `daily_loss_limit` → `oos_blocked` → `emergency_halt`

## Infrastructure de déploiement
- **Windows** : PM2 (`config/ecosystem.config.js`) + watchdog Python
- Heartbeat : `states/heartbeat.json` (fraîcheur < 10 minutes)
- Logs rotatifs : `code/logs/` (PM2) + `trading_bot.log` (Python logging)
- Max 5 restarts/heure, min 30s uptime pour compter comme démarrage réussi
```

---

### 1.3 `.claude/rules.md`
**Rôle** : contrat de comportement strict pour Claude sur ce projet.

```markdown
# MULTI_ASSETS — Règles Claude

## Règles de modification du code

### INTERDICTIONS ABSOLUES
1. **`except Exception: pass`** — toujours logger au minimum `logger.debug("[TAG] msg: %s", e)`
2. **Credentials en clair** — `config.api_key`, `config.secret_key` ne doivent jamais
   apparaître dans des logs, prints, ou f-strings non contrôlés
3. **`backtest_taker_fee` / `backtest_maker_fee` mutés au runtime** — ces valeurs sont le
   "golden standard" de reproductibilité ; seule une modification accompagnée d'un nouveau
   benchmark est acceptable
4. **Variable `start_date` figée à l'import** — utiliser `_fresh_start_date()`
5. **`TRAILING_STOP_MARKET` sur Spot** — ce type n'existe pas, lève `NotImplementedError`
6. **Accès à `bot_state[pair]` en écriture sans `with _bot_state_lock:`**
7. **BUY sans placement immédiat du stop-loss** — la chaîne achat→SL est atomique

### OBLIGATIONS
1. Après chaque modification de fichier `.py` : valider la syntaxe avec
   `python -c "import ast; ast.parse(open('...').read())"`
2. Après une correction de bug : lancer `pytest tests/ -x -q`
3. Pour une nouvelle feature : proposer un test dans `tests/` avant ou en même temps
4. Pour tout changement dans `bot_config.py` : vérifier la rétrocompatibilité de
   `Config.from_env()` (les clés env sont fixées par le `.env` de prod)
5. Pour tout changement dans `state_manager.py` : vérifier `_KNOWN_PAIR_KEYS` et
   `_KNOWN_GLOBAL_KEYS` — ajouter les nouvelles clés dans ces sets

## Règles de réponse

### Ordre de priorité lors d'un conflit
1. **Sécurité du capital** (stop-loss garanti, emergency halt, daily limit)
2. **Thread-safety** (verrous corrects)
3. **Intégrité de l'état** (HMAC, idempotence)
4. **Reproductibilité du backtest** (fees figés, pas de look-ahead)
5. Qualité de code (lisibilité, DRY)

### Niveau de changement
- Ne modifier que ce qui est explicitement demandé
- Ne pas refactorer du code fonctionnel adjacent à un bug fix
- Ne pas ajouter de docstrings ou type annotations sur du code non touché
- Ne pas créer de fichiers Markdown pour documenter chaque changement

### Format de diff proposé
Toujours montrer : fichier, lignes concernées, ancien code → nouveau code.
Inclure 3-5 lignes de contexte autour du changement.

## Règles de test

### Quoi tester
- Toute fonction qui touche `bot_state`, `save_bot_state`, `load_bot_state`
- Toute fonction qui appelle `safe_market_buy` ou `safe_market_sell`
- Toute logique de protection du capital (daily limit, oos_blocked, emergency_halt)
- Toute sérialisation/désérialisation d'état

### Conventions de test
- Mocker `BinanceFinalClient` avec `pytest-mock` — jamais d'appel API réel en test
- Utiliser des `tmp_path` pytest pour les fichiers d'état temporaires
- Les tests de state doivent vérifier l'intégrité HMAC post-sauvegarde

## Règles de déploiement (ne pas faire sans confirmation)
- `git push` ou modification de `config/ecosystem.config.js`
- Modification du `.env` de production
- `pm2 restart` ou `pm2 delete`
- Modification de `states/bot_state.json` directement
```

---

## 2. Architecture de la connaissance projet

### 2.1 `architecture/system_design.md`
**Contenu recommandé** (structure, pas de rédaction complète car manuel à 70%) :

```markdown
# System Design — MULTI_ASSETS Bot

## Architecture globale
[diagramme pipeline : Data → Indicators → Backtest → Signal → Order → State]

## Composants et flux de données
### Pipeline par cycle (2 min)
1. Scheduler `schedule.every(2).minutes` → `execute_scheduled_trading()`
2. Pour chaque paire : ThreadPoolExecutor (max 5 workers)
3. `monitor_and_trade_for_pair(pair_info)` :
   a. Throttle backtest (config.backtest_throttle_seconds = 3600s)
   b. `fetch_historical_data()` → cache pickle ou API
   c. `prepare_base_dataframe()` → OHLCV validé
   d. `run_all_backtests()` → 4 scénarios × walk-forward
   e. OOS gates → `oos_blocked` si échec
   f. `generate_buy/sell_checker()` → closure
   g. Décision + sizing + ordre + SL + journal

## Gestion des erreurs et récupération
[schéma : exceptions.py hierarchy → handlers → email → state]

## Concurrence
[schéma : threads par paire + locks]
```

> **Ce qui peut être généré** : noms des modules, dépendances, flux d'appels (95% depuis le code).  
> **Ce qui nécessite une rédaction manuelle** : les décisions d'architecture (pourquoi Spot et pas Futures, pourquoi schedule et pas asyncio, etc.)

---

### 2.2 `architecture/decisions.md`
**Structure ADR recommandée** — 7 décisions déjà prises dans ce code :

```markdown
# Architecture Decision Records — MULTI_ASSETS

## ADR-001 : JSON+HMAC au lieu de pickle signé
- **Statut** : Appliqué (C-17, migration complète)
- **Contexte** : fichiers `.pkl` non lisibles humainement, risque de désérialisation
- **Décision** : JSON_V1 avec HMAC-SHA256, clé = BINANCE_SECRET_KEY
- **Conséquences** : migration automatique, _StateEncoder pour Decimal/datetime

## ADR-002 : Backtest fees figés séparément des fees live
- **Statut** : Appliqué (P2-FEES)
- **Contexte** : les fees BNB peuvent varier ; le backtest doit être reproductible
- **Décision** : `backtest_taker_fee`, `backtest_maker_fee` gelés à 0.07%/0.02%
- **Conséquences** : jamais écrasé par get_binance_trading_fees() en production

## ADR-003 : Trailing stop manuel au lieu de TRAILING_STOP_MARKET
- **Statut** : Décision définitive
- **Contexte** : TRAILING_STOP_MARKET = Futures uniquement, pas disponible sur Spot
- **Décision** : implémentation manuelle dans monitor_and_trade_for_pair() avec max_price
- **Conséquences** : risque de gap entre cycles de 2 min (acceptable vs Futures)

## ADR-004 : Token bucket 18 req/s au lieu de sleep(n)
- **Statut** : Appliqué (C-05)
- **Contexte** : limite Binance Spot = 1200 req/min = 20 req/s
- **Décision** : TokenBucket(rate=18, capacity=18) dans BinanceFinalClient
- **Conséquences** : marge 10%, thread-safe, pas de sleep fixe

## ADR-005 : Cython en opt-in avec fallback Python (C-14)
- **Statut** : Appliqué
- **Contexte** : `.pyd` absent si compilation ratée (CI/CD, nouveau dev)
- **Décision** : flags CYTHON_BACKTEST_AVAILABLE / CYTHON_INDICATORS_AVAILABLE
- **Conséquences** : dégradation gracieuse, pas de crash au démarrage

## ADR-006 : RLock global + locks par paire (C-01)
- **Statut** : Appliqué
- **Contexte** : Plusieurs threads peuvent accéder à bot_state simultanément
- **Décision** : _bot_state_lock (RLock global) + _pair_execution_locks[pair]
- **Conséquences** : pas de deadlock (RLock réentrant), isolation par paire

## ADR-007 : Walk-forward ancré avec OOS gates calibrés crypto
- **Statut** : Appliqué (P1-THRESH)
- **Contexte** : Sharpe crypto OOS rarement > 1.0 ; WinRate structurellement bas sur trend-following
- **Décision** : Sharpe≥0.8, WinRate≥30%, decay≥0.15 (ratio OOS/FS)
- **Conséquences** : référence Bailey et al. (2014), Lopez de Prado (2018) Ch.12
```

---

### 2.3 `knowledge/trading_constraints.md`
**Contenu réel basé sur le code** :

```markdown
# Contraintes de trading Binance — MULTI_ASSETS

## API Binance Spot
| Contrainte | Valeur | Source code |
|-----------|--------|------------|
| Rate limit | 1200 req/min | `_TokenBucket(rate=18.0)` dans exchange_client.py |
| recvWindow | 60 000 ms | `config.recv_window = 60000` |
| Timeout requête | 45s | `kwargs['requests_params'] = {'timeout': 45}` |
| Resync horloge | toutes les 30 min | `schedule.every(30).minutes.do(_periodic_timestamp_resync)` |
| Offset timestamp | calculé dynamiquement | `real_offset - 500ms safety margin`, clampé [-10s, +1s] |

## Filtres de paire (LOT_SIZE, MIN_NOTIONAL)
- **LOT_SIZE** : chaque `quantity` doit être un multiple entier de `stepSize`
  - La quantité est arrondie par `(qty // stepSize) * stepSize` via `Decimal`
  - Utilisé dans `reconcile_positions_with_exchange()` et `_execute_buy()`
- **MIN_NOTIONAL** : valeur minimale d'un ordre en USDC
  - Backtest : `config.backtest_min_notional = 5.0 USDC` (simulé)
  - Live : vérifié via `get_symbol_filters()` → filtre `MIN_NOTIONAL`
- **MIN_QTY** : quantité minimale (souvent 0.001 pour les altcoins majeurs)
  - Seuil de réconciliation : `coin_balance >= 0.001` (dust < 0.001)

## Types d'ordres disponibles sur Spot
| Type | Disponible | Note |
|------|-----------|------|
| MARKET | ✅ | `safe_market_buy/sell` |
| STOP_LOSS_LIMIT | ✅ | Stop-loss exchange (C-02) |
| TRAILING_STOP_MARKET | ❌ | **Futures uniquement** — NotImplementedError |

## Fees
| Type | Valeur config | Utilisation |
|------|-------------|-------------|
| `taker_fee` | 0.0007 (0.07%) | frais live dans les ordres MARKET |
| `maker_fee` | 0.0002 (0.02%) | frais live dans les ordres LIMIT |
| `backtest_taker_fee` | 0.0007 | **FIGÉ** — jamais modifié par live |
| `backtest_maker_fee` | 0.0002 | **FIGÉ** — jamais modifié par live |
| `slippage_buy` | 0.0001 (0.01%) | slippage simulé en backtest |
| `slippage_sell` | 0.0001 (0.01%) | slippage simulé en backtest |

## Idempotence des ordres
- Chaque ordre a un `origClientOrderId` (UUID) généré avant le premier try
- Avant chaque retry (attempt > 0) : appel `get_order(origClientOrderId=...)`
- Si `FILLED` ou `PARTIALLY_FILLED` → retourne l'ordre existant sans re-soumettre
- Protection contre le double achat/vente en cas de timeout réseau

## Contraintes de sizing
- **Risk-based** (défaut): `qty = (equity × risk_pct) / (atr_multiplier × ATR × entry_price)`
  - `risk_pct = 5.5%`, `atr_stop_multiplier = 3.0×ATR`
- **Fixed notional**: `qty = notional_usdc / entry_price` (défaut: 10% equity)
- **Volatility parity**: `qty` ajustée pour cibler `target_volatility_pct = 2%` annualisé
- Retourne 0.0 si `ATR ≤ 0`, `entry_price ≤ 0`, or `equity ≤ 0` (pas d'exception — SizingError)

## Gestion du surplus de capital
- `initial_wallet = 10 000 USDC` (capital de référence)
- `daily_loss_limit_pct = 5%` → seuil = 500 USDC de perte journalière
- `partial_threshold_1 = 2%` → sortie partielle de 50% à +2%
- `partial_threshold_2 = 4%` → sortie partielle de 30% à +4%
- `trailing_activation_pct = 3%` → activation du trailing manuel
```

---

## 3. Fichiers de tâches réutilisables

### 3.1 `tasks/audit_system.md`

```markdown
# Audit System — MULTI_ASSETS
*Template de session d'audit. Cocher chaque item avant de déclarer "produit ready".*

## BLOC 1 — Biais Backtest
- [ ] `start_date` calculée avec `_fresh_start_date()` à chaque cycle (pas de variable figée)
- [ ] Indicateurs calculés avec `shift(1)` sur les bougies 4h pour le MTF filter
- [ ] StochRSI utilise `iloc[-2]` (bougie fermée) pas `iloc[-1]` (bougie en cours)
- [ ] Fees backtest (`backtest_taker_fee`) jamais écrasées par `get_binance_trading_fees()`
- [ ] `MIN_NOTIONAL=5 USDC` simulé dans le backtest (filtre LOT_SIZE respecté)
- [ ] Walk-forward ancré (anchored, pas rolling) avec au minimum 3 folds
- [ ] OOS gates actifs : Sharpe≥0.8, WinRate≥30%, decay ratio≥0.15
- [ ] `WF_SCENARIOS` est la source unique (pas de liste inline dupliquée)

## BLOC 2 — Thread Safety
- [ ] Toute écriture dans `bot_state` dans un bloc `with _bot_state_lock:`
- [ ] Pas de lecture-modification-écriture non atomique sur `bot_state`
- [ ] `_pair_execution_locks[pair]` acquis avant chaque `monitor_and_trade_for_pair()`
- [ ] `_oos_alert_lock` présent autour de `_oos_alert_last_sent` (lecture ET écriture)
- [ ] `indicators_cache` accès protégé par `_indicators_cache_lock`
- [ ] `_exchange_info_cache` dans `data_fetcher.py` : accès concurrents sûrs ?
- [ ] `save_bot_state()` thread-safe via `_bot_state_lock` ✓

## BLOC 3 — Sécurité Credentials
- [ ] `Config.__repr__` masque `api_key` et `secret_key` (C-10)
- [ ] Aucun `print(config)` ou `logger.info(f"config: {config}")` en clair
- [ ] `BINANCE_SECRET_KEY` utilisé uniquement comme clé HMAC (pas loggé)
- [ ] Pas de credentials dans les fichiers de cache ou l'état (bot_state.json)
- [ ] Emails d'alerte ne contiennent pas de clés API

## BLOC 4 — Robustesse Exchange
- [ ] `safe_market_buy/sell` : idempotence via `origClientOrderId` avant retry
- [ ] `place_exchange_stop_loss` : lève `OrderError` au lieu de retourner `None` si échec
- [ ] Après BUY raté : `pair_state['in_position']` reste False
- [ ] Après BUY réussi + SL raté : `safe_market_sell` de clôture déclenché
- [ ] `get_spot_balance_usdc` lève `BalanceUnavailableError` (pas de "balance=0" silencieux)
- [ ] `reconcile_positions_with_exchange()` appelé au démarrage après `load_bot_state()`
- [ ] C-11 : repose automatique du SL si position ouverte sans stop sur Binance
- [ ] Annulation du SL exchange (F-1) avant vente partielle/signal pour débloquer les coins

## BLOC 5 — Intégrité de l'état
- [ ] `bot_state.json` au format `JSON_V1:` + HMAC-SHA256
- [ ] `StateError` levée sur corruption → démarrage avec état vide + alerte email
- [ ] Nouvelles clés PairState dans `_KNOWN_PAIR_KEYS` (state_manager.py)
- [ ] Nouvelles clés globales dans `_KNOWN_GLOBAL_KEYS`
- [ ] `save_bot_state(force=True)` après chaque changement critique (achat, vente, SL)
- [ ] 3 échecs consécutifs de save → `emergency_halt = True`

## BLOC 6 — Protection du Capital
- [ ] `_is_daily_loss_limit_reached()` appelé avant chaque achat dans `_execute_buy()`
- [ ] `_update_daily_pnl()` appelé après chaque vente (stop-loss ET signal)
- [ ] `emergency_halt` vérifié en début de chaque cycle dans la boucle principale
- [ ] `oos_blocked` préservé au redémarrage (C-05 : pas purgé par `load_bot_state`)
- [ ] `partial_taken_1/2` réinitialisés uniquement après une vente totale

## BLOC 7 — Gestion d'erreurs
- [ ] Zéro `except Exception: pass` dans le code de production
- [ ] `log_exceptions` decorator sur les fonctions non-critiques (retour `default_return`)
- [ ] Fonctions critiques (SL, état) avec gestion explicite, pas de decorator silencieux
- [ ] Emails d'alerte avec cooldown `config.email_cooldown_seconds = 300s`
- [ ] Watchdog : heartbeat.json fraîcheur < 600s avant considérer le bot comme hung

## BLOC 8 — Tests
- [ ] `pytest tests/ -x -q` passe en vert (0 failures)
- [ ] Tests de corruption dans `TestCorruptionRobustness` (6 tests)
- [ ] Mocks API dans tous les tests exchange (pas d'appel réseau réel)
- [ ] Tests de sizing couvrent : ATR=0, entry_price=0, equity=0
- [ ] Test d'idempotence de `safe_market_buy` (ordre déjà exécuté avant retry)
```

---

### 3.2 `tasks/audit_email_alerts.md`

```markdown
# Audit Email Alerts — MULTI_ASSETS

## Système d'envoi
- [ ] `send_email_alert()` : retry 3× avec backoff (`@retry_with_backoff`)
- [ ] Sujet automatiquement préfixé `[MULTI_ASSETS]` via `config.project_name`
- [ ] SMTP TLS (starttls sur port 587) — pas de SSL direct
- [ ] `send_trading_alert_email()` : ajoute le solde SPOT USDC courant au corps
- [ ] Cooldown entre alertes similaires : `config.email_cooldown_seconds = 300`

## Couverture des événements critiques
- [ ] Achat exécuté → `buy_executed_email()` → `send_trading_alert_email()`
- [ ] Vente exécutée → `sell_executed_email()` → `send_trading_alert_email()`
- [ ] Échec connexion API → `api_connection_failure_email()`
- [ ] Erreur récupération données → `data_retrieval_error_email()`
- [ ] Erreur réseau → `network_error_email()`
- [ ] Erreur indicateurs → `indicator_error_email()`
- [ ] Erreur exécution trading → `trading_execution_error_email()`
- [ ] Erreur critique démarrage → `critical_startup_error_email()`
- [ ] Exception générique (via `log_exceptions`) → `generic_exception_email()`
- [ ] Position orpheline → alerte CRITIQUE dans `reconcile_positions_with_exchange()`
- [ ] Échec sauvegarde état → alerte CRITIQUE dans `save_bot_state()`
- [ ] Watchdog abandonne → `_notify_watchdog_stopped()`
- [ ] Daily loss limit atteint → alerte email ? (à vérifier — warning log actuel seulement)
- [ ] OOS gate bloqué → alerte email dans `apply_oos_quality_gate()` (cooldown 300s)

## Sécurité des emails
- [ ] Aucun credential dans le corps des emails
- [ ] Aucune exception silencieuse dans `send_trading_alert_email()`
- [ ] `add_spot_balance=False` quand le solde n'est pas pertinent (ex: email watchdog)
- [ ] Corps des emails contient suffisamment d'info pour diagnostiquer sans logs

## Cas manquants identifiés
- [ ] Vérifier : daily_loss_limit reached → email ou seulement log ?
- [ ] Vérifier : `init_timestamp_solution()` retourne False → email ou continue silencieusement ?
- [ ] Vérifier : `BalanceUnavailableError` → cycle sauté avec ou sans email ?
```

---

### 3.3 `tasks/correct_p0.md`

```markdown
# Template Correction P0 — MULTI_ASSETS

## Procédure standard pour une correction P0

### 1. Identification
```
Fichier(s) concerné(s) :
Lignes :
Symptôme :
Impact production :
Dépendances (autres P0 à corriger avant) :
```

### 2. Lecture du code avant modification
- Lire le fichier complet (ou au minimum 50 lignes autour du problème)
- Identifier toutes les références au code concerné (grep dans le workspace)
- Vérifier s'il existe un test existant qui couvre ce chemin

### 3. Correction
Respecter le contrat du fichier `.claude/rules.md` :
- Thread-safety si `bot_state` est touché
- Idempotence si un ordre exchange est placé
- `force=True` sur `save_bot_state()` si position change
- `try/except` explicite (pas de decorator silencieux sur les chemins critiques)

### 4. Validation obligatoire
```powershell
# Syntaxe
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/<fichier>.py').read()); print('OK')"
# Tests complets
pytest tests/ -x -q
# Si nouveau test créé :
pytest tests/test_<nouveau>.py -v
```

### 5. Checklist de livraison P0
- [ ] Code ne lève plus le bug décrit
- [ ] Cas nominal toujours fonctionnel (test existant passe)
- [ ] Nouveau test créé pour le cas d'erreur (non-régression)
- [ ] `bot_state` cohérent après la correction (pas d'état fantôme)
- [ ] Email d'alerte envoyé au bon niveau (CRITICAL, WARNING, INFO)
- [ ] Aucun log de credential introduit

### 6. Exemples de patterns P0 déjà appliqués dans le projet
| P0 | Pattern appliqué |
|----|-----------------|
| P0-01 (SL non garanti) | `OrderError` + `safe_market_sell` d'urgence + `sl_exchange_placed` persisté |
| P0-02 (balance 0 silencieux) | `BalanceUnavailableError` → cycle sauté, pas de buy avec balance=0 |
| P0-03 (OOS gate bypassé) | `oos_blocked` persisté au redémarrage (C-05), pas purgé par load |
| P0-04 (HMAC hardcodé) | Clé HMAC = BINANCE_SECRET_KEY, EnvironmentError si absente |
| P0-05 (SizingError silencieuse) | `SizingError` levée proprement, catchée dans `_execute_buy()` |
| P0-SAVE (save silencieux) | 3 failures → emergency_halt, alerte CRITICAL email |
```

---

## 4. Agents spécialisés

### 4.1 `agents/quant_engineer.md`

```markdown
---
description: Agent spécialisé en validité statistique des backtests et signaux trading
---

# Quant Engineer — MULTI_ASSETS

Tu es un ingénieur quantitatif expert en crypto trend-following.
Tu connais parfaitement ce codebase et ses contraintes.

## Ta priorité absolue
Détecter et corriger les biais qui rendent un backtest non-prédictif en live :
- **Look-ahead bias** : données futures utilisées pour calculer un signal passé
- **Data snooping** : optimisation sur le même dataset que le test
- **Survivorship bias** : sélection des paires après avoir vu leur performance
- **Fee bias** : frais backtest non alignés avec le live

## Ce que tu vérifies en priorité sur ce projet
1. `shift(1)` sur les bougies 4h dans `_compute_mtf_bullish()` (backtest_runner.py)
2. `iloc[-2]` vs `iloc[-1]` dans les signaux (signal_generator.py)
3. `backtest_taker_fee` jamais écrasé par `get_binance_trading_fees()`
4. Walk-forward ancré (anchored) et non circulaire dans walk_forward.py
5. OOS gates calibrés pour le crypto (Sharpe 0.8, WinRate 30%, pas les seuils équités)
6. `WF_SCENARIOS` est la source unique (pas de duplication inline)
7. `stoch_rsi_buy_min/max` depuis config (pas hardcodé dans le backtest)

## Métriques de référence (benchmarks validés)
- Calmar max observé : 2.004 (risk_per_trade=5.5%)
- Sharpe OOS attendu : 0.6-0.9 sur crypto trend-following
- WinRate attendu : 30-45% (les gagnants compensent par le profit factor)
- Max Drawdown cible : <25%

## Ce que tu ne modifies PAS sans benchmark complet
- `atr_multiplier` (8.0), `atr_stop_multiplier` (3.0)
- `oos_sharpe_min`, `oos_win_rate_min`, `oos_decay_min`
- `stoch_rsi_sell_exit` (0.4), `risk_per_trade` (5.5%)

## Ton output standard
Pour tout audit de biais : `[BIAIS DÉTECTÉ]` ou `[✓ PAS DE BIAIS]` avec la ligne de code concernée.
```

---

### 4.2 `agents/risk_manager.md`

```markdown
---
description: Agent spécialisé en protection du capital et robustesse exchange
---

# Risk Manager — MULTI_ASSETS

Tu es un risk manager spécialisé en trading algorithmique spot crypto.
Tu te concentres sur ce qui peut faire perdre du capital réel, pas simulé.

## Séquence de protection du capital (dans l'ordre)
1. `daily_loss_limit_pct=5%` → bloque les achats si -500 USDC/jour
2. `oos_blocked=True` → bloque les achats si OOS gates non passés
3. `emergency_halt=True` → arrête tout trading
4. Stop-loss exchange natif (`STOP_LOSS_LIMIT`) → protection position individuelle
5. Trailing stop manuel → take-profit progressif

## Ce que tu vérifies sur chaque PR/modification
- **BUY sans SL** : après `safe_market_buy()`, `place_exchange_stop_loss()` est-il appelé ?
  Si `OrderError` → `safe_market_sell()` immédiat déclenché ?
- **Balance=0 silencieuse** : `get_spot_balance_usdc()` lève-t-il `BalanceUnavailableError` ?
- **Daily PnL** : `_update_daily_pnl()` appelé après CHAQUE vente (SL et signal) ?
- **Idempotence** : un ordre peut-il être soumis deux fois en cas de timeout ?
- **Coins bloqués** : `_cancel_exchange_sl()` (F-1) appelé avant vente partielle ?
- **Réconciliation** : `reconcile_positions_with_exchange()` au démarrage ?

## Scénarios de risque critiques
| Scénario | Protection attendue |
|----------|-------------------|
| Crash bot après achat, avant SL | Réconciliation au restart (C-03, C-11) |
| Timeout réseau pendant un ordre | Idempotence via origClientOrderId |
| save_state() échoue 3× | emergency_halt + email CRITICAL |
| Balance USDC non récupérable | BalanceUnavailableError → cycle sauté |
| Perte > 5% en une journée | daily_loss_limit → achats bloqués |
| Backtest ne passe pas OOS gates | oos_blocked = True → pas d'achat |

## Ton output standard
Pour tout audit de risque : `[RISQUE CAPITAL]`, `[RISQUE EXCHANGE]`, ou `[✓ PROTÉGÉ]`.
Toujours traiter `[RISQUE CAPITAL]` avant `[RISQUE EXCHANGE]`.
```

---

### 4.3 `agents/code_auditor.md`

```markdown
---
description: Agent spécialisé en sécurité, concurrence et qualité du code
---

# Code Auditor — MULTI_ASSETS

Tu es un auditeur de code Python expert en systèmes concurrents et sécurité.

## Checklist automatique à chaque audit

### Concurrence
- Toute écriture `bot_state[x] = ...` dans `with _bot_state_lock:` ?
- `_pair_execution_locks[pair]` acquis avant `monitor_and_trade_for_pair()` ?
- `_oos_alert_lock` autour de `_oos_alert_last_sent` (lecture AND écriture) ?
- `indicators_cache` (LRU OrderedDict) protégé par `_indicators_cache_lock` ?
- Pas de `time.sleep()` long dans un bloc `with lock:` ?

### Sécurité des credentials
- `config.api_key` / `config.secret_key` dans un log, print, f-string ? → CRITIQUE
- `_HMAC_KEY` (state_manager.py) jamais loggé ? ✓
- Emails générés par `email_templates.py` : contiennent-ils des clés ? → Vérifier

### Gestion d'erreurs
- `except Exception: pass` → INTERDIT
- `except Exception as e: pass` → INTERDIT
- `@log_exceptions` sur des chemins critiques (BUY, SL) → WARNING (préférer try/except explicite)
- Erreurs de sauvegarde d'état silencieuses → CRITIQUE

### Qualité
- Duplication de listes inline vs utilisation de `WF_SCENARIOS` ?
- Constantes hardcodées (frais, seuils OOS) au lieu de `config.*` ?
- `start_date` figée en variable module-level ?
- `import` circulaire potentiel entre modules P3-SRP ?

### Convention de nommage
- Fonctions privées extraites P3-SRP préfixées `_` dans leur module d'origine
- Wrappers dans MULTI_SYMBOLS.py injectent les globals (client, send_alert)
- TypedDict `PairState` utilisé pour les annotations, pas pour l'accès runtime

## Sévérités
- `[CRITIQUE]` : peut faire perdre du capital ou crasher le bot
- `[HIGH]` : risque de bug silencieux en production
- `[MEDIUM]` : dette technique, dégradation future
- `[LOW]` : lisibilité, convention

## Ton workflow
1. Lire le fichier en entier avant de commenter
2. Lister tous les problèmes par sévérité
3. Proposer les corrections dans l'ordre CRITIQUE → HIGH → MEDIUM
4. Valider syntaxe + tests après chaque correction
```

---

## 5. Contextes par module

### Structure recommandée

Pour chaque module critique, créer un fichier `code/src/<module>.context.md` (ou `docs/modules/<module>.md`) :

**Pattern type** (à instancier pour chaque module) :

```markdown
# <Module> — Contexte

## Responsabilité unique
[1 phrase]

## Ce module FAIT
- …

## Ce module NE FAIT PAS (délégué à)
- …

## Contrats critiques
- [invariants que le module GARANTIT]

## Thread-safety
- [locks utilisés, accès concurrents]

## Exceptions levées
- [liste des exceptions avec conditions]

## Dépendances entrantes / sortantes
```

**Contenus spécifiques recommandés pour les 5 modules les plus critiques** :

| Module | Contexte essentiel |
|--------|------------------|
| `exchange_client.py` | Token bucket 18 req/s · idempotence via origClientOrderId · pas de TRAILING_STOP_MARKET · offset horloge dynamique |
| `state_manager.py` | JSON_V1 + HMAC-SHA256 · _KNOWN_PAIR_KEYS · StateError sur corruption · pas de fallback hardcodé pour HMAC_KEY |
| `backtest_runner.py` | Fees figés · shift(1) MTF · Cython first avec flag · WF_SCENARIOS source unique |
| `walk_forward.py` | OOS gates calibrés crypto · anchored walk-forward · métriques OOS vs FS · référence Lopez de Prado |
| `MULTI_SYMBOLS.py` | Orchestrateur central · wrappers inject globals · bot_state global · lifecycle du bot |

---

## 6. Plan de migration

### Ordre recommandé (impact décroissant)

| Priorité | Fichier | Effort | Source | Impact par session |
|---------|---------|--------|--------|--------------------|
| 1 | `.github/copilot-instructions.md` | **15 min** | 95% généré | Élimine les erreurs courantes dès le 1er prompt |
| 2 | `.claude/rules.md` | **20 min** | 90% généré | Contrôle immédiat du comportement de Claude |
| 3 | `.claude/context.md` | **30 min** | 80% généré | Contexte projet complet sans re-explanation |
| 4 | `tasks/audit_system.md` | **25 min** | 85% généré | Checklist réutilisable à chaque sprint |
| 5 | `agents/code_auditor.md` | **15 min** | 95% généré | Persona dédié pour les sessions d'audit |
| 6 | `agents/risk_manager.md` | **15 min** | 95% généré | Persona dédié pour la sécurité capital |
| 7 | `agents/quant_engineer.md` | **15 min** | 90% généré | Persona dédié anti-biais backtest |
| 8 | `tasks/correct_p0.md` | **15 min** | 80% généré | Template reproductible pour les P0 |
| 9 | `tasks/audit_email_alerts.md` | **15 min** | 80% généré | Audit email dédié |
| 10 | `knowledge/trading_constraints.md` | **30 min** | 75% généré | Évite les re-confirmations Binance API |
| 11 | `architecture/decisions.md` | **60 min** | 50% manuel | Mémoire long-terme des décisions techniques |
| 12 | `architecture/system_design.md` | **45 min** | 40% manuel | Diagramme pipeline complet |
| 13 | `code/src/*.context.md` (5 modules) | **10 min × 5** | 85% généré | Accélération sur les modules les plus touchés |

### Ce qui peut être généré automatiquement depuis le code
- Contenu de `.github/copilot-instructions.md` : paramètres `Config`, noms de modules, règles d'interdiction
- Contenu des `tasks/` : patterns tirés des `docs/PLAN_ACTION.md` et de l'historique d'audit
- Contenu des `agents/` : checklist tirées directement des constantes et structures du code
- Contenu des `*.context.md` : docstrings, imports, exceptions déjà présents dans chaque module

### Ce qui nécessite une rédaction manuelle
- `architecture/system_design.md` : le "pourquoi" de chaque choix (Spot vs Futures, schedule vs asyncio)
- `architecture/decisions.md` : contexte décisionnel (ce qui a été évalué et rejeté)
- Compléter les cas manquants dans `tasks/audit_email_alerts.md` (daily_loss_limit → email ?)

---

### Recommandation d'exécution

**Semaine 1 — Base immédiate (items 1–5, ~1h30 total)**
- Crée les 3 fichiers IA (`copilot-instructions.md`, `rules.md`, `context.md`) + `audit_system.md` + `code_auditor.md`
- Impact : dès la prochaine session, Claude n'a plus besoin du résumé de conversation pour être opérationnel

**Semaine 2 — Agents et tâches (items 6–9, ~1h)**
- Complète les 2 agents restants + les 2 templates de tâches

**Semaine 3+ — Architecture et modules (items 10–13, à faire au fil des sessions)**
- Les contextes de modules peuvent être créés **au moment où on travaille sur un module** — c'est le moment où le contexte est le plus frais
