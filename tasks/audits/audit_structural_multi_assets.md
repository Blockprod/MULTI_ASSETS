# AUDIT STRUCTUREL MULTI_ASSETS — Rapport Complet

**Date** : 2026-03-18  
**Auditeur** : GitHub Copilot (Claude Sonnet 4.6)  
**Périmètre** : Structure, organisation des modules, couplage, interfaces, dette technique, configuration

---

## BLOC 1 — Pipeline Réel

```
ENTRY POINT: execute_scheduled_trading() [MULTI_SYMBOLS.py:1155]
       │
       ▼
1. FETCH OHLCV
   _fetch_historical_data() [MULTI_SYMBOLS.py:921]
   → data_fetcher.fetch_historical_data()
   → Cache pickle TTL 30j [cache_manager.py:241]
   → Retour : pd.DataFrame(close, high, low, volume, time)
       │
       ▼
2. CALCUL INDICATEURS
   calculate_indicators() [indicators_engine.py:223]
   (wrapper redondant dans MULTI_SYMBOLS.py:935)
   → EMA fast/slow, RSI, StochRSI [indicators_engine.py:75]
   → ATR, ADX, MACD, TRIX (optionnels selon scénario)
   → Cache LRU [indicators_engine.py:69]
   → Cython via backtest_engine_standard.pyd si dispo
   → Retour : DataFrame enrichi + np.ndarray[float64]
       │
       ▼
3. BACKTEST WALK-FORWARD
   run_parallel_backtests() [MULTI_SYMBOLS.py:993]
   → backtest_from_dataframe() [backtest_runner.py:94]
   → 4 scénarios WF_SCENARIOS (StochRSI, +SMA200, +ADX, +TRIX)
   → OOS gates : Sharpe≥0.8, WinRate≥30%, Decay≥0.15 [walk_forward.py:362]
   → Retour : Dict[str, Any] {calmar, sharpe, win_rate, trades[], ...}
       │
       ▼
4. SIGNAL → ORDRE BINANCE
   execute_real_trades() [MULTI_SYMBOLS.py:2985]
   → generate_buy_condition_checker() [signal_generator.py:54]
   → safe_market_buy() [exchange_client.py:342]
   → Placement SL STOP_LOSS_LIMIT immédiat
   → Retour : orderId (str | int)
   → Log : trade_journal.jsonl [trade_journal.py:39]
       │
       ▼
5. ÉTAT PERSISTÉ
   save_bot_state() throttled 5s [MULTI_SYMBOLS.py:455]
   → Format JSON_V1 + HMAC-SHA256 [state_manager.py:46, 117]
   → Clé HMAC : BINANCE_SECRET_KEY
   → states/bot_state.json
   → TypedDict PairState + _KNOWN_PAIR_KEYS [state_manager.py:54]
```

### 1.1 Types de données en transit

| Étape | Type | Classe/Interface | Fichier | Détails |
|---|---|---|---|---|
| OHLCV | DataFrame | `pd.DataFrame` | cache_manager.py | close, high, low, volume, time |
| Indicateurs | ndarray + DataFrame | `np.ndarray[float64]`, `pd.DataFrame` | indicators_engine.py | EMA, RSI, StochRSI, ATR |
| Backtest résultat | dict | `Dict[str, Any]` | backtest_runner.py | calmar, sharpe, trades[], equity |
| Paramètres | dict | `Dict[str, Any]` | — | ema1, ema2, scenario, timeframe |
| PairState | TypedDict | `PairState` | MULTI_SYMBOLS.py:224 | entry_price, stop_loss, sl_order_id |
| BotState | dict | `Dict[str, Any]` | MULTI_SYMBOLS.py:282 | `bot_state[pair]` = PairState |
| Ordre API | dict | `Dict[str, Any]` | exchange_client.py | `{orderId, status, fills[], ...}` |

### 1.2 Divergence vs architecture déclarée

Le fichier `MULTI_SYMBOLS.py` contient des wrappers (lignes 935, 387, 390) qui redoublent
`indicators_engine.py` et `exchange_client.py` — doublon non documenté dans l'architecture déclarée.

---

## BLOC 2 — Séparation des Responsabilités

### 2.1 Grandes fonctions (> 100 lignes)

| # | Fonction | Fichier:Ligne | Lignes est. | Responsabilités mêlées |
|---|---|---|---|---|
| 1 | `execute_real_trades` | MULTI_SYMBOLS.py:2985 | ~500 | fetch + signal + BUY + SL + partial + trailing + journal |
| 2 | `_execute_buy` | MULTI_SYMBOLS.py:2425 | ~400 | sizing + ordre + SL + OOS gates + email |
| 3 | `backtest_from_dataframe` | backtest_runner.py:94 | ~750 | parcours candles + sizing + states |
| 4 | `reconcile_positions_with_exchange` | MULTI_SYMBOLS.py:600 | ~320 | inventory check + repose SL |
| 5 | `execute_scheduled_trading` | MULTI_SYMBOLS.py:1155 | ~300 | backtest + affichage + persistence + planning |
| 6 | `_execute_signal_sell` | MULTI_SYMBOLS.py:2212 | ~200 | partial + SL + trailing entremêlés |
| 7 | `calculate_indicators` | indicators_engine.py:223 | ~150 | indicateurs + Cython delegation + cache |

### 2.2 Violations SRP identifiées

| ID | Violation | Fichier:Ligne | Sévérité | Détails |
|---|---|---|---|---|
| SRP-1 | `MULTI_SYMBOLS.py` contient 50+ fonctions | MULTI_SYMBOLS.py:1–3600 | **P0** | Orchestration + backtest + trading live + state + persistence + alerts + display |
| SRP-2 | `execute_real_trades()` mêle 5+ responsabilités | MULTI_SYMBOLS.py:2985 | **P0** | Devrait être split : Fetch→Décision→Exécution→Persistence |
| SRP-3 | `_execute_buy()` : 5 responsabilités différentes | MULTI_SYMBOLS.py:2425 | **P0** | sizing + placement + SL + OOS gates + journal |
| SRP-4 | `place_stop_loss_order` local vs exchange_client | MULTI_SYMBOLS.py:380 vs exchange_client.py:503 | **P1** | Wrapper local utilise client global ; exchange_client attend client en param |
| SRP-5 | `compute_stochrsi` dupliqué | MULTI_SYMBOLS.py:312 vs indicators_engine.py:75 | **P1** | Legacy non supprimé — 2 implémentations à garder synchrones |
| SRP-6 | `backtest_from_dataframe` gère sizing externe | backtest_runner.py:94 | **P1** | Devrait déléguer aux modules `position_sizing` |
| SRP-7 | `_execute_signal_sell()` gère partial + SL + trailing | MULTI_SYMBOLS.py:2212 | **P1** | 3 responsabilités : logique, placement, comptabilité |
| SRP-8 | `reconcile_positions_with_exchange()` : check + repose SL | MULTI_SYMBOLS.py:600 | **P1** | Mêle inventory check et order placement |

### 2.3 Patterns d'accès Config & bot_state

**Config (Singleton global) :**
- Initialisé : `config = Config.from_env()` [bot_config.py:308]
- Importé via `from bot_config import config` dans 15+ modules — **accès global, pas d'injection de dépendances**
- Callback circuit-breaker : `set_error_notification_callback()` [bot_config.py:34]
- **Problème** : Couplage tight de 15+ modules au singleton global

**bot_state (Dict global + RLock) :**
- Défini : `bot_state: Dict[str, Any] = {}` [MULTI_SYMBOLS.py:282]
- Protégé : `_bot_state_lock = threading.RLock()` [MULTI_SYMBOLS.py:302]
- 20+ mutations directes sous lock [L:685, 799, 1336, 2714, 2765, ...]
- Persistence : `save_bot_state(force=False)` throttled 5s [L:455]
- **Problème** : Pas de porte unique d'écriture — mutations éclatées dans tout MULTI_SYMBOLS

**Client Binance :**
- Initialisé en local à MULTI_SYMBOLS.py:189, passé en paramètre à exchange_client uniquement
- Wrappers locaux `safe_market_buy` / `safe_market_sell` [MULTI_SYMBOLS.py:387/390] redélèguent à exchange_client via client global — couplage inutile

### 2.4 Dépendances circulaires

**Aucune détectée.** Flux unidirectionnel :

```
MULTI_SYMBOLS.py → backtest_runner, indicators_engine, exchange_client,
                   signal_generator, walk_forward, state_manager, bot_config
                   (pas de back-import)
```

---

## BLOC 3 — Dette Technique

| # | Problème | Fichier:Ligne | Analyse |
|---|---|---|---|
| D-1 | `compute_stochrsi` dupliqué | MULTI_SYMBOLS.py:312 vs indicators_engine.py:75 | Legacy non supprimé. 2 copies à maintenir synchrones. Risque de divergence silencieuse. |
| D-2 | `place_stop_loss_order` redondant | MULTI_SYMBOLS.py:380 vs exchange_client.py:503 | Wrapper local avec client global vs interface paramétrée. Maintenance double. |
| D-3 | Wrappers `safe_market_buy/sell` locaux | MULTI_SYMBOLS.py:387/390 | Redélèguent à exchange_client via client global — coupling inutile, doublon d'interface. |
| D-4 | `in_position` (bool) déprécié | MULTI_SYMBOLS.py:257 | Toujours écrit/lu mais `last_order_side` est la source de vérité. Deux sources conflictuelles. |
| D-5 | `config/trades_export.csv` commité | config/ | Référencé uniquement dans des fichiers doc. Contient des données trading réelles — **sécurité**. |
| D-6 | Rotation `bot_state.json.bak` manuelle | states/ | Aucun appel détecté à `.bak` dans le code Python. Pas de rotation automatique. |
| D-7 | Pas de `RotatingFileHandler` pour trading_bot.log | code/logs/ | `watchdog.py` l'utilise (5 MB / 3 backups) ; le bot principal ne le fait pas. |
| D-8 | `MULTI_SYMBOLS.py` ~3600 lignes | MULTI_SYMBOLS.py:1–3600 | God Object. Split partiel (modules extraits) mais orchestrateur reste hub de tout. |
| D-9 | `calculate_indicators` wrapper redondant | MULTI_SYMBOLS.py:935 | Wrappe indicators_engine.py sans valeur ajoutée — couche intermédiaire inutile. |

---

## BLOC 4 — Configuration & Environnements

### 4.1 Variables d'env — validation au démarrage

| Variable | Validée | Exception levée | Défaut |
|---|---|---|---|
| `BINANCE_API_KEY` | **OUI** — bot_config.py:136 | `EnvironmentError` | aucun |
| `BINANCE_SECRET_KEY` | **OUI** — bot_config.py:140 + state_manager.py:28 | `EnvironmentError` | aucun |
| `SENDER_EMAIL` | **OUI** — bot_config.py:132 | `ValueError` | aucun |
| `RECEIVER_EMAIL` | **OUI** — bot_config.py:144 | `ValueError` | aucun |
| `GOOGLE_MAIL_PASSWORD` | **OUI** — bot_config.py:152 | `ValueError` | aucun |
| `TAKER_FEE` | Non | — | `'0.0007'` |
| `MAKER_FEE` | Non | — | `'0.0002'` |
| `BACKTEST_TAKER_FEE` | Non | — | `'0.0007'` (figé P2-FEES) |
| `RISK_PER_TRADE` | Non | — | `'0.055'` |
| `DAILY_LOSS_LIMIT_PCT` | Non | — | `'0.05'` |

Toutes les variables critiques sont validées dans `Config.from_env()` — **aucun démarrage silencieux possible**.

### 4.2 Mode démo / production

**AUCUN mode démo détecté.** Pas de flag `--dry-run`, pas de variable `BOT_MODE`.

- `start_safe.ps1` : lock file + exécution directe de MULTI_SYMBOLS.py sans garde
- `config/ecosystem.config.js` (PM2) : idem, exécution directe
- **Conséquence** : un premier démarrage place des ordres réels immédiatement sur Binance

### 4.3 Cohérence PM2 ↔ start_safe.ps1

| Fichier | Contenu | Cohérence |
|---|---|---|
| `ecosystem.config.js` | Script MULTI_SYMBOLS.py, CWD code/src | Cohérent avec start_safe.ps1 ✅ |
| `start_safe.ps1` | Lock file + Python exec MULTI_SYMBOLS.py | Cohérent ✅ |
| `pyproject.toml` | Ruff + pytest config | OK, E402 supprimé pour modules sys.path ✅ |
| `config/setup.py` | Cython build (indicators.pyx, backtest_engine.pyx) → code/bin/ | Pas de doublon avec pyproject.toml ✅ |
| `requirements.txt` | Dépendances Python | À jour ✅ |

---

## SYNTHÈSE — Tableau des Problèmes

| ID | Bloc | Problème | Fichier:Ligne | Sévérité | Impact | Effort |
|---|---|---|---|---|---|---|
| **P0-1** | SRP | `execute_real_trades` : 5+ responsabilités | MULTI_SYMBOLS.py:2985 | **P0** | Impossible de tester signal sans ordres réels, bug SL contaminent tout | Haut |
| **P0-2** | SRP | God Object MULTI_SYMBOLS.py (~3600 lignes) | MULTI_SYMBOLS.py:1–3600 | **P0** | Maintenance impossible, merge conflicts garantis | Très haut |
| **P0-3** | Config | Aucun mode démo / dry-run | (architecture gap) | **P0** | Ordres réels dès le premier démarrage | Moyen |
| **P1-1** | Dette | `compute_stochrsi` dupliqué | MULTI_SYMBOLS.py:312 vs indicators_engine.py:75 | **P1** | 2 copies à synchroniser, risque de divergence | Bas |
| **P1-2** | SRP | `place_stop_loss_order` wrapper redondant | MULTI_SYMBOLS.py:380 vs exchange_client.py:503 | **P1** | Deux interfaces différentes pour la même opération | Bas |
| **P1-3** | SRP | `reconcile_positions_with_exchange` : fetch + pose SL | MULTI_SYMBOLS.py:600 | **P1** | Test unitaire difficile, 2 responsabilités | Moyen |
| **P1-4** | Dette | `in_position` déprécié encore utilisé | MULTI_SYMBOLS.py:257 | **P1** | 2 sources de vérité sur la position | Moyen |
| **P2-1** | Sécurité | `trades_export.csv` commité avec données réelles | config/ | **P2** | Données trading sensibles dans le repo | Bas |
| **P2-2** | Archi | Config singleton non injecté (global) | bot_config.py:308 | **P2** | Couplage tight 15+ modules, tests difficiles | Haut |
| **P2-3** | Archi | `bot_state` muté directement depuis MULTI_SYMBOLS | MULTI_SYMBOLS.py:282 | **P2** | Pas de porte unique d'accès | Moyen |
| **P3-1** | Infra | Pas de `RotatingFileHandler` pour trading_bot.log | code/logs/ | **P3** | Log sans limite de taille | Bas |
| **P3-2** | Infra | Rotation `.bak` manuelle | states/ | **P3** | Pas de sauvegarde automatique de l'état | Bas |

---

## Top 3 — Problèmes Structurels Bloquants

### 1. P0 — `execute_real_trades` : moteur monolithique
**Fichier:Ligne** : MULTI_SYMBOLS.py:2985, ~500 lignes

Mêle en une seule fonction : fetch balances, lecture état, calcul signal, placement BUY, pose SL,
partial exits, trailing stop, journal, persistence état.

**Impossible de tester la logique de signal sans risquer d'exécuter un ordre réel.**
Un bug dans la partie SL contamine l'ensemble de la chaîne.

Refactor cible :
```python
def execute() -> None:
    state = fetch_current_state()       # read-only
    signal = check_signal(state)        # pure logic
    if signal:
        order = execute_order(state, signal)  # side effect isolé
        update_state(order)             # persistence
```

---

### 2. P0 — God Object `MULTI_SYMBOLS.py` (~3600 lignes)
**Fichier:Ligne** : MULTI_SYMBOLS.py:1–3600

Un seul fichier contient : orchestration backtest, trading live, état global, wrappers redondants,
templates email, fonctions d'affichage, logging inline.

Le split en modules externes (indicators, exchange, walk_forward…) est une bonne base,
mais l'orchestrateur reste le hub de tout. Toute modification nécessite de naviguer ~3600 lignes.

Découpage recommandé :
- `orchestrator.py` : pipeline backtest / scheduling
- `trading_engine.py` : `execute_real_trades` décomposé
- `order_manager.py` : BUY / SELL / SL logic
- `state_sync.py` : réconciliation positions

---

### 3. P0 — Absence de mode démo
**Fichier:Ligne** : (architecture gap — aucun fichier source)

Aucun garde entre un démarrage de test et un démarrage en production.
Le premier `run` peut placer des ordres réels sur Binance.

Remédiation recommandée :
```python
# bot_config.py
BOT_MODE: str = os.getenv('BOT_MODE', 'DEMO')  # défaut DEMO pour sécurité

# exchange_client.py
if config.bot_mode == 'DEMO':
    logger.info("[DRY-RUN] Ordre simulé — non transmis à Binance")
    return {"orderId": "DRYRUN-0", "status": "FILLED"}
```

---

## Points Solides à Conserver

| | Force | Fichier:Ligne | Raison |
|---|---|---|---|
| ✅ | **RLock thread-safety** | MULTI_SYMBOLS.py:302 | RLock global + locks par paire — thread-safety correctement implémentée |
| ✅ | **State HMAC-SHA256** | state_manager.py:46 | Intégrité garantie, auto-migration pickle → JSON, `StateError` sur mismatch |
| ✅ | **OOS walk-forward gates** | walk_forward.py:362 | Anti-overfitting expert : Sharpe≥0.8, WinRate≥30%, decay≥0.15 |
| ✅ | **Cython fallback graceful** | backtest_runner.py:80 | 30-50× speedup si .pyd dispo, sinon Python pur transparent |
| ✅ | **exchange_client production-grade** | exchange_client.py:80 | Timestamp sync, rate limiter 18 req/s, idempotence `origClientOrderId` |
| ✅ | **Watchdog + heartbeat** | watchdog.py:120 | Détection hang 10 min, redémarrage automatique |
| ✅ | **TypedDict PairState + _KNOWN_PAIR_KEYS** | state_manager.py:54 | Schéma d'état validable, compatible Mypy |
| ✅ | **Config.from_env() avec EnvironmentError** | bot_config.py:136 | Toutes les vars critiques validées au démarrage, pas de défaut silencieux |
| ✅ | **Email circuit-breaker** | error_handler.py:30 | Throttle 300 s entre alertes, thread-safe, anti-spam |
| ✅ | **Modularisation Phase SRP** | code/src/ | Split réussi : 15+ modules extraits (indicators, backtest, exchange, signals, walk_forward…) |

---

## Score d'Architecture

| Dimension | Score | Détail |
|---|---|---|
| Thread-Safety | 9/10 | RLock implémenté correctement. Pair locks fine-grain. |
| State Persistence | 9/10 | HMAC-SHA256, auto-migration, TypedDict validé. |
| Error Handling | 8/10 | Circuit-breaker, HMAC validation, Cython fallback. |
| Performance | 8/10 | Cython 30-50×, LRU cache, rate limiter. |
| Configuration | 8/10 | Singleton centralisé, vars critiques validées. Manque mode démo. |
| Documentation | 7/10 | Fichiers `.context.md` présents. Architecture déclarée incomplète. |
| Modularisation | 6/10 | Phase SRP amorcée mais MULTI_SYMBOLS reste hub central. |
| Maintenabilité | 5/10 | Orchestrateur 3600 lignes, duplication, wrappers redondants. |
| Testabilité | 4/10 | Fonctions monolithiques, état global, hard à mocker. |

---

*Rapport généré automatiquement — 2026-03-18*
