# PLAN D'ACTION — MULTI_ASSETS — 2026-03-19
Sources : `tasks/audits/AUDIT_TECHNIQUE_SECURITE_MULTI_ASSETS.md`
Total : 🔴 0 · 🟠 0 · 🟡 4 · Effort estimé : 3 jours

---

## PHASE 1 — CRITIQUES 🔴

Aucune.

---

## PHASE 2 — MAJEURES 🟠

Aucune.

---

## PHASE 3 — MINEURES 🟡

### [TS-P2-02] Réconciliation démarrage — alerte email + blocage achats si échouée
Fichier : `code/src/MULTI_SYMBOLS.py:1361-1362`
Problème : Le bloc `except` autour de `reconcile_positions_with_exchange()` dans `main()` se contente
  d'un `logger.error`. Si la réconciliation échoue (timeout API, exception réseau), le bot démarre
  avec un état potentiellement désynchronisé : une position orpheline peut rester sans SL, ou une
  vente peut tenter de s'exécuter sur un solde nul. Aucune alerte email n'est envoyée.
Correction :
  1. Dans le bloc `except Exception as reconcile_err` de `main()`, ajouter un appel
     `send_trading_alert_email(subject="[CRITIQUE] Réconciliation échouée au démarrage", ...)`.
  2. Poser un flag `bot_state['reconcile_failed'] = True` sous `_bot_state_lock` + `save_bot_state(force=True)`.
  3. Dans `_execute_real_trades_inner()`, avant la logique d'achat, vérifier
     `bot_state.get('reconcile_failed')` → si True : `logger.warning` + `return` immédiat (achats bloqués).
  4. Dans `reconcile_positions_with_exchange()` (ou après son appel réussi dans `main()`), remettre
     `bot_state['reconcile_failed'] = False` et sauvegarder.
  5. Ajouter `'reconcile_failed'` dans `_KNOWN_PAIR_KEYS` ou dans la section globale de
     `validate_bot_state()` dans `state_manager.py`.
Validation :
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/state_manager.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 590/590 pass (+ nouveaux tests TS-P2-03/04 si exécutés avant)
Dépend de : Aucune
Statut : ✅

---

### [TS-P2-03] Test E2E — chaîne SL-fail → rollback → emergency_halt
Fichier : `tests/test_order_manager_sl_chain.py` (nouveau) · `code/src/order_manager.py:1463,1513`
Problème : Le chemin critique `SL échoué → rollback safe_market_sell → si rollback échoue →
  set_emergency_halt()` n'est pas couvert par un test d'intégration complet. Une régression
  dans `order_manager.py` (refactoring, changement d'API mock) passerait inaperçue sur ce
  chemin qui protège le capital en dernier recours.
Correction :
  Créer `tests/test_order_manager_sl_chain.py` avec au minimum 3 cas :
    1. `test_sl_fail_triggers_rollback` :
       - Mock `_place_exchange_stop_loss` levant une exception
       - Vérifier que `safe_market_sell` (rollback) est appelé avec les bons paramètres
    2. `test_sl_fail_rollback_fail_triggers_emergency_halt` :
       - Mock `_place_exchange_stop_loss` levant une exception
       - Mock `safe_market_sell` (rollback) levant aussi une exception
       - Vérifier que `set_emergency_halt()` est appelé avec un message explicite
       - Vérifier qu'un email critique est envoyé (`send_trading_alert_email` mocké)
    3. `test_sl_success_no_emergency_halt` :
       - Mock `_place_exchange_stop_loss` retournant un ordre valide
       - Vérifier que `set_emergency_halt()` n'est PAS appelé
  Utiliser les fixtures `mock_binance_client` de `conftest.py` et patcher les dépendances
  via `unittest.mock.patch`.
Validation :
  .venv\Scripts\python.exe -m pytest tests/test_order_manager_sl_chain.py -v
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : ≥ 593/593 pass (590 + 3 nouveaux)
Dépend de : Aucune
Statut : ✅

---

### [TS-P2-04] Tests réconciliation — position_reconciler avec mock API Binance
Fichier : `tests/test_position_reconciler.py` (nouveau) · `code/src/position_reconciler.py`
Problème : `position_reconciler.py` n'a aucun test injectant un compte Binance mock avec
  des positions réelles pour vérifier la détection de positions orphelines. Une régression
  dans `_check_pair_vs_exchange()` ou `_handle_pair_discrepancy()` passerait inaperçue.
Correction :
  Créer `tests/test_position_reconciler.py` avec au minimum 4 cas :
    1. `test_orphan_position_detected` :
       - `bot_state` : paire sans BUY (`last_order_side=None`)
       - Mock `client.get_account()` : `coinBalance > 0` pour cette paire
       - Vérifier que `_check_pair_vs_exchange()` retourne un statut "orphan"
       - Vérifier que `_handle_pair_discrepancy()` pose un SL ou envoie une alerte
    2. `test_ghost_position_detected` :
       - `bot_state` : paire avec `last_order_side='BUY'`
       - Mock `client.get_account()` : `coinBalance = 0` pour cette paire
       - Vérifier que la discordance est détectée et loguée
    3. `test_coherent_position_no_action` :
       - `bot_state` : paire avec `last_order_side='BUY'`
       - Mock compte : `coinBalance > 0` correspondant
       - Vérifier qu'aucune action corrective n'est déclenchée
    4. `test_no_position_no_action` :
       - `bot_state` : paire sans BUY
       - Mock compte : `coinBalance = 0`
       - Vérifier qu'aucune action n'est déclenchée
  Utiliser `_ReconcileDeps` avec des mocks injectés (pas de patching global).
Validation :
  .venv\Scripts\python.exe -m pytest tests/test_position_reconciler.py -v
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : ≥ 594/594 pass (590 + 4 nouveaux, ou combiné avec TS-P2-03)
Dépend de : Aucune
Statut : ✅

---

### [TS-P2-01] Circuit breaker — outage Binance prolongé
Fichier : `code/src/exchange_client.py`
Problème : En cas d'outage Binance prolongé (429 / 503 répétés sur plusieurs minutes),
  les retries `@retry_with_backoff` continuent pour chaque paire jusqu'à épuisement de
  `max_retries` sans mise en quarantaine du flux. Avec 10 paires en parallèle et 5 retries
  chacune, cela génère 50 appels bloqués simultanément et sature les workers PM2.
Correction :
  Implémenter un circuit breaker minimaliste dans `exchange_client.py` :
    1. Ajouter un compteur d'échecs consécutifs `_circuit_failure_count: int = 0`
       et un timestamp `_circuit_open_until: float = 0.0` (module-level, protégés par `_lock`).
    2. Dans `_request()`, avant l'envoi :
       - Si `time.time() < _circuit_open_until` → lever `CircuitOpenError`
         (ou retourner une sentinel value) sans appeler l'API.
    3. Après chaque échec réseau/HTTP 429/5xx dans `_request()` :
       - Incrémenter `_circuit_failure_count`
       - Si `_circuit_failure_count >= CIRCUIT_BREAKER_THRESHOLD` (défaut : 10) →
         `_circuit_open_until = time.time() + CIRCUIT_BREAKER_RESET_SECONDS` (défaut : 60s)
    4. Après chaque succès : réinitialiser `_circuit_failure_count = 0`.
    5. Ajouter `CIRCUIT_BREAKER_THRESHOLD: int = 10` et `CIRCUIT_BREAKER_RESET_SECONDS: int = 60`
       dans `bot_config.py` (configurable via env vars `CIRCUIT_BREAKER_THRESHOLD`,
       `CIRCUIT_BREAKER_RESET_SECONDS`).
    6. Lors de l'ouverture du circuit : `logger.critical("[CIRCUIT-BREAKER] Circuit ouvert ...")` +
       `send_trading_alert_email(...)`.
  Note : ne pas modifier `@retry_with_backoff` — le circuit breaker opère en amont dans `_request()`.
Validation :
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/exchange_client.py').read()); print('OK')"
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/bot_config.py').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : ≥ X/X pass selon tests ajoutés
Dépend de : Aucune
Statut : ✅

---

## SÉQUENCE D'EXÉCUTION

```
1. [TS-P2-02]  ← priorité haute (impact capital), modification ciblée, 2 fichiers
2. [TS-P2-03]  ← test unitaire pur, aucune dépendance, validation immédiate
3. [TS-P2-04]  ← test unitaire pur, aucune dépendance, validation immédiate
4. [TS-P2-01]  ← plus complexe, modifier exchange_client.py + bot_config.py, en dernier
```

Validation finale après toute la séquence :
```powershell
.venv\Scripts\python.exe -m pytest tests/ -x -q
```

---

## CRITÈRES PASSAGE EN PRODUCTION

- [x] Zéro 🔴 ouvert
- [x] `pytest tests/` : 685/685 pass (2026-03-19)
- [x] Zéro credential dans les logs
- [x] Stop-loss garanti après chaque BUY
- [x] Réconciliation failure → email + achats bloqués (TS-P2-02)
- [x] Test E2E SL-fail → emergency_halt présent et vert (TS-P2-03)
- [x] Tests position_reconciler présents et verts (TS-P2-04)
- [ ] Paper trading validé 5 jours minimum

---

## TABLEAU DE SUIVI

| ID | Titre | Sévérité | Fichier | Effort | Statut | Date |
|----|-------|----------|---------|--------|--------|------|
| TS-P2-02 | Réconciliation démarrage — email + blocage achats | 🟡 P2 | `MULTI_SYMBOLS.py:1361-1362` · `state_manager.py` | 0.5 j | ✅ | 2026-03-19 |
| TS-P2-03 | Test E2E SL-fail → rollback → emergency_halt | 🟡 P2 | `tests/test_order_manager_sl_chain.py` (nouveau, 3 tests) | 0.5 j | ✅ | 2026-03-19 |
| TS-P2-04 | Tests réconciliation position_reconciler | 🟡 P2 | `tests/test_position_reconciler.py` (nouveau, 9 tests) | 1 j | ✅ | 2026-03-19 |
| TS-P2-01 | Circuit breaker outage Binance | 🟡 P2 | `exchange_client.py` · `bot_config.py` · `exceptions.py` · `MULTI_SYMBOLS.py` | 1 j | ✅ | 2026-03-19 |

---

## RÉSULTAT FINAL

**Tests :** 685/685 passed (2026-03-19)

**Nouveaux fichiers créés :**
- `tests/test_circuit_breaker.py` — 21 tests (TS-P2-01)
- `tests/test_position_reconciler.py` — 7 tests (TS-P2-04)
- `tests/test_order_manager_sl_chain.py` — 3 tests (TS-P2-03)

**Modifications code :**
- `code/src/MULTI_SYMBOLS.py` — flag `reconcile_failed`, blocage achats, email alerte (TS-P2-02)
- `code/src/state_manager.py` — `reconcile_failed` dans `_KNOWN_GLOBAL_KEYS` (TS-P2-02)
- `code/src/exchange_client.py` — `_CircuitState`, `CircuitOpenError` (TS-P2-01)
- `code/src/bot_config.py` — `circuit_breaker_threshold`, `circuit_breaker_reset_seconds` (TS-P2-01)
