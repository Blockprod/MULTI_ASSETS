# Audit Technique & Sécurité — MULTI_ASSETS

**Date** : 2026-03-19  
**Auteur** : Agent (perspective Senior Security Engineer)  
**Périmètre** : `code/src/` — 5 blocs : Credentials · Thread-Safety · Robustesse Binance · Persistance · Tests  
**Baseline tests** : 590/590 ✅

---

## BLOC 1 — SÉCURITÉ CREDENTIALS

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| `api_key`/`secret_key` chargés exclusivement via `os.getenv('BINANCE_API_KEY'/'BINANCE_SECRET_KEY')` — aucune valeur en dur | `bot_config.py:137-138` | ✅ CONFORME |
| `preload_data.py` identique : `os.getenv("BINANCE_API_KEY", "")` | `preload_data.py:17` | ✅ CONFORME |
| `Config.__repr__` masque systématiquement : `api_key=***MASKED***, secret_key=***MASKED***` | `bot_config.py:117` | ✅ CONFORME |
| Aucun fragment de clé dans les logs (grep `logger.*.api_key` / `logger.*.secret` → 0 match) | tous `code/src/` | ✅ CONFORME |
| Guard email : `_SENSITIVE = {'api_key', 'secret_key', 'secret', 'password', 'token'}` — filtre sur tous les corps d'alertes | `email_templates.py:265` | ✅ CONFORME |
| `.env` explicitement en `.gitignore` — jamais committée | `.gitignore` | ✅ CONFORME |
| `states/` exclu du dépôt git (contient `bot_state.json` avec HMAC signé) | `.gitignore` | ✅ CONFORME |

**Bilan Bloc 1 : 🔴 0 · 🟠 0 · 🟡 0** — aucune lacune détectée.

---

## BLOC 2 — THREAD-SAFETY

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| `_bot_state_lock = threading.RLock()` — global, réentrant, protège toutes les mutations | `MULTI_SYMBOLS.py:329` | ✅ CONFORME |
| `_pair_execution_locks` — dict de `threading.Lock` par paire + `_pair_locks_mutex` pour le dict | `MULTI_SYMBOLS.py:331-332` | ✅ CONFORME |
| Acquire non-bloquant par paire : `_pair_lock.acquire(blocking=False)` → skip si déjà en cours | `MULTI_SYMBOLS.py:1028` | ✅ CONFORME |
| Toutes les mutations critiques `bot_state` sous `with _bot_state_lock` (7 points vérifiés) | `MULTI_SYMBOLS.py:432,507,551,567,826,1053,1350` | ✅ CONFORME |
| `bot_state.get('emergency_halt')` lu sans lock (L1044) — lecture atomique dict Python (GIL), non muté à ce point | `MULTI_SYMBOLS.py:1044,1048` | ✅ CONFORME (GIL) |
| `_pair_locks_mutex` acquis avant d'accéder au dict `_pair_execution_locks` | `MULTI_SYMBOLS.py:1022-1024` | ✅ CONFORME |
| Flags `partial_taken_1/2` idempotents : mis à `True` même si exécution bloquée (empêche retry dangereux) | `backtest_runner.py:520,543` | ✅ CONFORME |
| Locks dédiés aux alertes throttle : `_oos_alert_lock`, `_daily_loss_alert_lock`, `_sl_missing_alert_lock`, `_drawdown_alert_lock` | `MULTI_SYMBOLS.py:834-845` | ✅ CONFORME |
| `ErrorHandler._lock = threading.RLock()` + `_history_lock = threading.Lock()` | `error_handler.py:61,119` | ✅ CONFORME |
| Cache indicateurs protégé : `_indicators_cache_lock = threading.Lock()` | `indicators_engine.py:70` | ✅ CONFORME |
| Client Binance : `_lock = threading.Lock()` + `_tickers_lock = threading.Lock()` | `exchange_client.py:38,614` | ✅ CONFORME |

**Bilan Bloc 2 : 🔴 0 · 🟠 0 · 🟡 0** — aucune lacune détectée.

---

## BLOC 3 — ROBUSTESSE BINANCE ET GESTION ERREURS

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| Rate limiter : `_TokenBucket(rate=18.0, capacity=18.0)` — acquis à chaque `_request()` | `exchange_client.py:59,155,702` | ✅ CONFORME |
| `@retry_with_backoff` avec jitter uniforme `[0, base_delay]` — anti thundering-herd | `bot_config.py:351-379` | ✅ CONFORME |
| Appliqué sur `data_fetcher` (3 retries, 2s) et `cache_manager` (5 retries, 2s) | `data_fetcher.py:96`, `cache_manager.py:184` | ✅ CONFORME |
| `_server_time_offset` dynamique, clampé `max(-10000, min(1000, offset))`, fallback conservateur -2000ms | `exchange_client.py:69,125,133` | ✅ CONFORME |
| Gestion erreur Binance `-1021` (timestamp) avec re-sync automatique dans `_request()` | `exchange_client.py:179` | ✅ CONFORME |
| `recvWindow=60000ms` centralisé dans `bot_config.py:71`, sanitization anti-doublon dans `_request()` | `exchange_client.py:156-167` | ✅ CONFORME |
| `origClientOrderId` utilisé dans `safe_market_buy` ET `safe_market_sell` → idempotence au retry | `exchange_client.py:380,430` | ✅ CONFORME |
| `TRAILING_STOP_MARKET` défini mais lève `NotImplementedError` explicitement — non appelable depuis le live | `exchange_client.py:472-481`, `MULTI_SYMBOLS.py:366-370` | ✅ CONFORME |
| SL échoué → rollback `safe_market_sell` → si rollback aussi échoue → `set_emergency_halt()` + email critique | `order_manager.py:1463,1513` | ✅ CONFORME |
| Aucun `except Exception: pass` silencieux sur chemins critiques (P1-06 re-raise) | `state_manager.py:172` | ✅ CONFORME |
| **Circuit breaker pattern (open/half-open/closed) absent** | (absent) | 🟡 TS-P2-01 |

**🟡 TS-P2-01 — Circuit breaker absent**  
`exchange_client.py` utilise un `TokenBucket` (rate limiting) et `@retry_with_backoff` (jitter). Mais il n'existe pas de circuit breaker standard (pattern ouvert/semi-ouvert/fermé) : si Binance subit une indisponibilité prolongée (429 ou 5xx répétés sur plusieurs minutes), les retries continuent indéfiniment jusqu'au seuil `max_retries`, sans mise en quarantaine temporaire du flux. Le bot peut saturer ses threads sans jamais se stabiliser.

**Bilan Bloc 3 : 🔴 0 · 🟠 0 · 🟡 1**

---

## BLOC 4 — PERSISTANCE ET RÉCUPÉRATION

| Point | Fichier:Ligne | Verdict |
|-------|--------------|---------|
| Écriture atomique : `tmp_path = state_path + '.tmp'` + `os.replace()` | `state_manager.py:154-157` | ✅ CONFORME |
| HMAC-SHA256 sur `BINANCE_SECRET_KEY` — pas de clé hardcodée, bot ne démarre pas si absent | `state_manager.py:25-38` | ✅ CONFORME |
| `StateError` levée si HMAC invalide au chargement (P1-06 re-raise, pas de swallow) | `state_manager.py:205-211` | ✅ CONFORME |
| `StateError` au démarrage → email via `load_bot_state()` L501 → `_error_notification_handler` → `send_trading_alert_email` | `MULTI_SYMBOLS.py:497-506` | ✅ CONFORME |
| Migration automatique des formats : JSON signé → pickle signé (HMAC_V1) → plain JSON → pickle non signé | `state_manager.py:200-245` | ✅ CONFORME |
| Backup `.bak` avant chaque écriture dans `save_bot_state()` (C-11) | `MULTI_SYMBOLS.py:436-441` | ✅ CONFORME |
| `validate_bot_state()` — validation schéma structurelle (non bloquante, log warning) | `state_manager.py:248` | ✅ CONFORME |
| Kill-switch `emergency_halt` persisté dans `bot_state` signé HMAC → survit aux redémarrages | `state_manager.py:73` | ✅ CONFORME |
| 3 échecs `save_bot_state` consécutifs → email critique + `emergency_halt` activé (P0-SAVE) | `MULTI_SYMBOLS.py:420,456,459` | ✅ CONFORME |
| Réconciliation au démarrage : `reconcile_positions_with_exchange()` appelé dans `main()` | `MULTI_SYMBOLS.py:1360` | ✅ CONFORME |
| **Réconciliation en échec loguée seulement — pas d'email ni d'`emergency_halt`** | `MULTI_SYMBOLS.py:1361-1362` | 🟡 TS-P2-02 |

**🟡 TS-P2-02 — Réconciliation au démarrage : failure silencieuse**  
`main()` appelle `reconcile_positions_with_exchange(crypto_pairs)` dans un `try/except` qui se contente d'un `logger.error(f"[RECONCILE] Erreur...")`. Si la réconciliation échoue (timeout API, exception dans `position_reconciler`), le bot démarre avec un état potentiellement incohérent sans alerte email ni mode dégradé. Une position orpheline (achat avant crash, état non sauvegardé) resterait non détectée.  
*Correction suggérée* : ajouter un `send_trading_alert_email` dans le `except`, avec flag `reconcile_failed` dans `bot_state` pour bloquer les achats jusqu'à réconciliation manuelle.

**Bilan Bloc 4 : 🔴 0 · 🟠 0 · 🟡 1**

---

## BLOC 5 — TESTS

| Module / Scénario | Couverture | Fichier test | Ligne |
|-------------------|-----------|-------------|-------|
| `safe_market_buy/sell` avec mock client, rate limiter throttle | ✅ | `test_exchange_client.py` | — |
| Idempotence via `origClientOrderId` (retry safe) | ✅ | `test_exchange_client_idempotency.py` | — |
| `save_state` round-trip et fichier absent | ✅ | `test_state_manager.py` | L36,L44 |
| Pas d'écriture si état inchangé | ✅ | `test_state_manager.py` | L50 |
| Écriture atomique `.tmp` + `os.replace()` | ✅ | `test_state_manager.py` | L64 |
| **Corruption HMAC → `StateError`** (byte flipé) | ✅ | `test_state_manager.py` | L74 |
| Bytes aléatoires → `StateError` | ✅ | `test_state_manager.py` | L200 |
| Pickle signé falsifié → `StateError` | ✅ | `test_state_manager.py` | L313 |
| Migration pickle non signé (legacy) | ✅ | `test_state_manager.py` | L91 |
| Écritures concurrentes sans corruption | ✅ | `test_state_manager.py` | L104 |
| HMAC signature sur `BINANCE_SECRET_KEY` | ✅ | `test_api_keys.py` | L78 |
| Échecs `save_bot_state` (2 → alerte, 3 → halt) | ✅ | `test_phase1_fixes.py` | L158-216 |
| Permission error écriture état → halt | ✅ | `test_phase1_fixes.py` | L468 |
| `mock_binance_client` fixture centralisée | ✅ | `conftest.py` | L171 |
| Drawdown alert throttle per-pair | ✅ | `test_p2_05_specific.py` | — |
| **Chaîne complète SL-fail → rollback → `emergency_halt`** | ❌ Non trouvé | — | TS-P2-03 |
| **Réconciliation avec mock API Binance (positions ouvertes)** | ❌ Non trouvé | — | TS-P2-04 |

**🟡 TS-P2-03 — Test E2E chaîne SL-fail → rollback → emergency_halt manquant**  
Le chemin `order_manager.py:1463→1513` (SL échoué → rollback `safe_market_sell` → si rollback échoue → `set_emergency_halt()`) n'est pas couvert par un test d'intégration complet. Les composants individuels sont testés séparément, mais un test E2E avec mock de `safe_market_sell` levant deux exceptions successives garantirait qu'aucune régression n'affecte ce chemin critique capital.

**🟡 TS-P2-04 — Tests réconciliation `position_reconciler` avec mock API manquants**  
`position_reconciler.py` n'a pas de test injectant un compte Binance mock avec `coinBalance > 0` pour vérifier la détection de positions orphelines. Une régression dans `_check_pair_vs_exchange()` ou `_handle_pair_discrepancy()` passerait inaperçue.

**Bilan Bloc 5 : 🔴 0 · 🟠 0 · 🟡 2**

---

## SYNTHÈSE

### Tableau des findings

| ID | Bloc | Description | Fichier:Ligne | Sévérité | Impact | Effort |
|----|------|-------------|--------------|----------|--------|--------|
| TS-P2-01 | Robustesse Binance | Circuit breaker absent — outage prolongé Binance non géré | `exchange_client.py` | 🟡 P2 | Retries infinis sur 429/5xx pendant outage | M |
| TS-P2-02 | Persistance | Réconciliation démarrage failure silencieuse — pas d'email ni de blocage | `MULTI_SYMBOLS.py:1361-1362` | 🟡 P2 | Position orpheline non détectée → capital à risque | S |
| TS-P2-03 | Tests | Chaîne E2E SL-fail → rollback → emergency_halt non testée | `order_manager.py:1463-1513` | 🟡 P2 | Régression silencieuse sur chemin critique capital | S |
| TS-P2-04 | Tests | Tests réconciliation `position_reconciler` avec mock API absents | `position_reconciler.py` | 🟡 P2 | Régression silencieuse sur détection positions orphelines | S |

### Score global

**🔴 P0 : 0 · 🟠 P1 : 0 · 🟡 P2 : 4**

### Top 3 risques avant tout déploiement réel

1. **TS-P2-02** : Si la réconciliation échoue au démarrage (timeout Binance, exception réseau), le bot démarre sans le savoir avec un état désynchronisé. Une position longue existante peut être ignorée (pas de SL posé) ou une position fantôme peut déclencher une vente sur un solde nul. *Corriger en priorité avant la mise en production.*

2. **TS-P2-03** : L'absence de test E2E sur la chaîne `SL-fail → rollback → emergency_halt` signifie qu'une régression dans `order_manager.py` (refactoring, changement API) pourrait désactiver silencieusement le dernier filet de protection capital. Ce test est simple à écrire (2 mock exceptions successives sur `safe_market_sell`).

3. **TS-P2-01** : En cas d'outage Binance prolongé (10-30 min), les threads exécutant `execute_real_trades` retryeront jusqu'à `max_retries` en parallèle pour chaque paire. Avec 10 paires simultanées et 5 retries, cela génère 50 appels bloqués. Un circuit breaker (open après N échecs consécutifs, reset après 60s) éviterait la saturation des workers.

### Points forts à conserver

- **Gestion credentials** : zéro clé en dur, `__repr__` masqué, filtre email sur `_SENSITIVE`, `.env`/`states/` dans `.gitignore`. Architecture irréprochable.
- **Thread-safety** : RLock global + Lock par paire + acquire non-bloquant + 4 locks throttle dédiés — aucune race condition identifiée malgré l'architecture multi-thread.
- **Idempotence** : `origClientOrderId` sur `safe_market_buy/sell` garantit la sécurité des retries network-split. Couplé au `TokenBucket`, c'est une protection solide contre les ordres dupliqués.
- **Persistence HMAC** : écriture atomique (`.tmp` + `os.replace()`), signature HMAC-SHA256, `StateError` re-raised (pas de swallow), 3 échecs → `emergency_halt` + email, backup `.bak` systématique. Stack de persistance niveau production.
- **Couverture tests état** : 11 cas de test couvrant round-trip, HMAC tamper, pickle migration, concurrence, bytes aléatoires — couverture remarquable pour un composant critique.
