# PLAN D'ACTION — MULTI_ASSETS — 2026-03-19
Sources : tasks/audits/audit_email_alerts_multi_assets.md
Total : 🔴 0 · 🟠 4 · 🟡 7 · Effort estimé : 2-3 jours

---

## PHASE 1 — CRITIQUES 🔴

Aucun item critique (P0) dans cet audit.

---

## PHASE 2 — MAJEURES 🟠

### [EM-P1-01] Horodatage manquant dans les emails BUY/SELL

Fichier : `code/src/email_templates.py:170,204`
Problème : `buy_executed_email()` et `sell_executed_email()` n'incluent pas de timestamp, contrairement aux templates réseau/API qui appellent `_timestamp()`. Rend le diagnostic post-incident difficile (impossible de corréler email et log sans horodatage).
Correction : Ajouter une ligne `Horodatage : {_timestamp()}` dans le corps de `buy_executed_email()` (ligne ~170) et `sell_executed_email()` (ligne ~204), en réutilisant la fonction `_timestamp()` déjà présente dans le module.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/email_templates.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : Aucune
Statut : ✅

---

### [EM-P1-02] Double préfixage des sujets [MULTI_ASSETS] [BOT CRYPTO]

Fichier : `code/src/email_templates.py` (tous les templates avec `[BOT CRYPTO]`) + `code/src/email_utils.py:30`
Problème : `send_email_alert()` préfixe automatiquement le sujet avec `[{project}]` = `[MULTI_ASSETS]`. Les templates `email_templates.py` incluent déjà `[BOT CRYPTO]` dans leurs sujets. Résultat : `[MULTI_ASSETS] [BOT CRYPTO] Achat exécuté`. Les filtres email côté opérateur (règles Gmail/Outlook) sont cassés — impossible de filtrer sur un préfixe unique stable.
Correction : Supprimer le segment `[BOT CRYPTO]` des sujets dans `email_templates.py`. Conserver uniquement la catégorie sémantique (ex: `Achat exécuté`, `STOP-LOSS déclenché`). `send_email_alert()` ajoutant déjà `[MULTI_ASSETS]`, les sujets finaux seront `[MULTI_ASSETS] Achat exécuté`.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/email_templates.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : Aucune
Statut : ✅

---

### [EM-P1-03] Watchdog restart silencieux sur heartbeat stale

Fichier : `code/src/watchdog.py:211`
Problème : `restart_bot(reason="heartbeat_stale")` est appelé quand le fichier `heartbeat.json` est vieux de plus de 10 min (bot freeze/crash). Ce restart est **SILENCIEUX** — uniquement un `logger.warning`. L'opérateur ne reçoit un email que si le watchdog abandonne définitivement via `_notify_watchdog_stopped()`. Un crash suivi d'un restart normal passe totalement inaperçu.
Correction : Dans `restart_bot()` (watchdog.py:~184), avant de relancer le process, appeler `_send_email_alert()` (ou équivalent disponible dans watchdog) avec un sujet `[RESTART] Bot redémarré — {reason}` et le timestamp. Vérifier que le cooldown existant de `_notify_watchdog_stopped` est réutilisé ou qu'un nouveau cooldown est appliqué pour éviter les spams si restarts en boucle.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/watchdog.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q -k "watchdog"
```
Attendu : OK + 0 failures
Dépend de : Aucune
Statut : ✅

---

### [EM-P1-04] Position sans SL vérifiée seulement au shutdown

Fichier : `code/src/MULTI_SYMBOLS.py:1180` (référence `_verify_all_stops_on_shutdown`)
Problème : `_verify_all_stops_on_shutdown()` vérifie la présence d'un stop-loss actif sur Binance pour chaque position ouverte — mais uniquement lors de l'arrêt (SIGTERM, atexit). Entre deux cycles de 2 min, une position peut rester ouverte sans SL (ex: SL annulé manuellement, bug de placement, reconnexion Binance). Cette fenêtre de risque est indétectée.
Correction : Dans le cycle live (`_execute_live_trading_only` ou son équivalent), ajouter une vérification de `sl_exchange_placed` pour chaque paire `in_position`. Si `sl_exchange_placed=False` et position ouverte → déclencher l'alerte email + tentative de repose SL (pattern C-11 déjà implémenté). Ce check doit utiliser `_pair_execution_locks[pair]` et `_bot_state_lock`.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : Aucune (C-11 déjà présent — réutiliser la logique de repose SL)
Statut : ✅

---

## PHASE 3 — MINEURES 🟡

### [EM-P2-01] 3 emails en cascade sur save_bot_state() failures

Fichier : `code/src/MULTI_SYMBOLS.py:455`
Problème : `save_bot_state()` envoie un email à chaque échec (1er, 2ème, 3ème). Les 3 emails arrivent en ~5s (throttle 5s entre saves). `send_trading_alert_email()` contourne le throttle de `ErrorHandler`. L'opérateur reçoit un spam de 3 emails identiques en cas de problème disque.
Correction : Grouper les 3 messages en un seul email envoyé au 3ème échec (celui qui déclenche `emergency_halt`). Supprimer les emails des 1er et 2ème échecs, ou les remplacer par un log WARNING uniquement. Le 3ème email (CRITICAL) suffit à alerter.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : Aucune
Statut : ✅

---

### [EM-P2-02] Circuit breaker OUVERT sans notification dédiée

Fichier : `code/src/error_handler.py:71`
Problème : Quand le `CircuitBreaker` passe à l'état OPEN (3 échecs consécutifs, bot en mode PAUSED), aucun email dédié n'est envoyé. L'opérateur ne sait pas que le bot a stoppé ses opérations. Il ne sera alerté que par le prochain `handle_error()` individuel.
Correction : Dans `record_failure()` (error_handler.py:~71), quand `self._is_open()` devient True après incrémentation des échecs, appeler `self._notification_callback()` avec un sujet `[CIRCUIT OUVERT] Bot en mode PAUSED` et le contexte (nb échecs, dernière exception). Respecter le throttle `_EMAIL_COOLDOWN_SECONDS`.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/error_handler.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : Aucune
Statut : ✅

---

### [EM-P2-03] Breakeven et trailing activation sans notification email

Fichier : `code/src/order_manager.py` (logique trailing/breakeven)
Problème : L'activation du breakeven (déplacement du SL au prix d'entrée) et du trailing stop (ajustement dynamique du SL) sont loggés uniquement. Aucun email n'est envoyé à l'opérateur. Sans visibilité sur ces modifications de protection, le suivi d'une position active est impossible depuis l'interface email.
Correction : Ajouter un appel à `send_alert_fn()` (le callback déjà injecté dans order_manager) au moment de l'activation du breakeven et du premier ajustement trailing. Utiliser `send_trading_alert_email()` avec un message concis (paire, nouveau SL, raison). Appliquer un throttle par paire (1 email max par activation) pour éviter le spam sur trailing fréquent.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/order_manager.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : Aucune
Statut : ✅

---

### [EM-P2-04] Corps inline dans ~30% des alertes (non-templates)

Fichier : `code/src/error_handler.py:155` + occurrences dans `order_manager.py` / `backtest_orchestrator.py`
Problème : ~30% des alertes email construisent leur corps en ligne (f-string directement dans l'appelant) au lieu d'utiliser `email_templates.py`. Cela duplique la logique de formatage, rend la maintenance difficile et empêche un style cohérent.
Correction : Migrer les corps inline vers `email_templates.py` en créant des fonctions dédiées par type d'événement. Priorité : `error_handler.py:155` (corps le plus complexe). Les appelants passent uniquement les données brutes ; le template se charge du formatage.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/error_handler.py').read()); print('OK')"
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/email_templates.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : EM-P1-01, EM-P1-02 (pour cohérence de style avant migration)
Statut : ✅

---

### [EM-P2-05] Drawdown kill-switch absent

Fichier : Non existant (feature manquante)
Problème : Aucun mécanisme ne détecte qu'une position a perdu X% depuis l'entrée en dehors du stop-loss fixe. Si le SL est placé trop loin ou n'est pas déclenché pour une raison technique, la perte peut dépasser la tolérance sans que l'opérateur soit alerté. `emergency_halt` couvre les cas généraux mais pas un drawdown max par position.
Correction : Ajouter dans le cycle live (lors du check `sl_exchange_placed`) une vérification du PnL non réalisé : si `(current_price - entry_price) / entry_price < -max_drawdown_pct` → email d'alerte + log CRITICAL. Ne pas déclencher de vente automatique (risque de double-SL). `max_drawdown_pct` = nouvelle config dans `bot_config.py` (défaut suggéré : -0.15 = -15%).
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/bot_config.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : EM-P1-04 (le check de position doit être en place avant d'y ajouter le drawdown check)
Statut : ✅

---

### [EM-P3-01] Convention de sévérité non systématique dans les sujets email

Fichier : `code/src/email_templates.py` (tous les templates sujets)
Problème : Les sujets mélangent plusieurs conventions : `[BOT CRYPTO]`, `[ALERTE BOT CRYPTO]`, `[CRITIQUE]`, `[EMERGENCY HALT]`, `[BOT ALERT]`. Impossible de créer des règles de filtrage email fiables côté opérateur.
Correction : Adopter une convention unique sur le niveau de sévérité dans le sujet, par exemple : `[INFO]`, `[WARN]`, `[CRIT]`. Appliquer cette convention à tous les templates de `email_templates.py`. Exemples : `[INFO] BTC/USDC — Achat exécuté`, `[WARN] Circuit breaker ouvert`, `[CRIT] Emergency halt déclenché`.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/email_templates.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : EM-P1-02 (supprimer [BOT CRYPTO] d'abord)
Statut : ✅

---

### [EM-P3-02] generic_exception_email expose args/kwargs bruts

Fichier : `code/src/email_templates.py:254`
Problème : `generic_exception_email()` inclut `args` et `kwargs` bruts dans le corps de l'email. Si une fonction recevant des credentials (ex: clé API, mot de passe) est décorée `@log_exceptions` et lève une exception, ceux-ci pourraient apparaître dans l'email envoyé.
Correction : Dans `generic_exception_email()`, filtrer ou tronquer `args` et `kwargs` avant de les inclure dans le corps. Supprimer toute valeur ressemblant à une clé API (longueur > 32, hexa) ou exclure explicitement les paramètres nommés `api_key`, `secret`, `password`, `token`. Limiter à `str(args)[:200]`.
Validation :
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/email_templates.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : OK + 0 failures
Dépend de : Aucune
Statut : ✅

---

## SÉQUENCE D'EXÉCUTION

Ordre tenant compte des dépendances :

```
1. EM-P1-01  — Horodatage BUY/SELL (email_templates.py)
2. EM-P1-02  — Double préfixage (email_templates.py) — même fichier que EM-P1-01 → batch
3. EM-P3-02  — Sécurité args/kwargs (email_templates.py) — même fichier → batch
4. EM-P1-03  — Watchdog restart email (watchdog.py)
5. EM-P2-02  — Circuit breaker email (error_handler.py)
6. EM-P1-04  — Vérification SL cycle live (MULTI_SYMBOLS.py)
7. EM-P2-01  — Throttle cascade save_bot_state (MULTI_SYMBOLS.py) — même fichier → batch
8. EM-P2-05  — Drawdown alert (MULTI_SYMBOLS.py + bot_config.py) — dépend de EM-P1-04
9. EM-P2-03  — Breakeven/trailing email (order_manager.py)
10. EM-P3-01 — Convention sévérité sujets (email_templates.py) — dépend de EM-P1-02
11. EM-P2-04 — Migration corps inline vers templates — dépend de EM-P1-01, EM-P1-02
```

**Batch recommandé :**
- **Batch A** (email_templates.py) : EM-P1-01 + EM-P1-02 + EM-P3-02 → même fichier, zéro conflit
- **Batch B** (MULTI_SYMBOLS.py) : EM-P2-01 + EM-P1-04 → même fichier, à faire en ordre (P1-04 d'abord pour les locks)
- **Séquentiels** : EM-P1-03 (watchdog.py), EM-P2-02 (error_handler.py), EM-P2-03 (order_manager.py)
- **En dernier** : EM-P2-04 (migration templates), EM-P2-05 (feature), EM-P3-01 (convention)

---

## CRITÈRES PASSAGE EN PRODUCTION

- [x] Zéro 🔴 ouvert (N/A — aucun P0 dans cet audit)
- [x] Zéro 🟠 ouvert (EM-P1-01 à EM-P1-04 résolus)
- [x] `pytest tests/ -x -q` : 685/685 pass (2026-03-19)
- [x] Zéro credential dans les corps d'email (EM-P3-02 résolu)
- [x] Stop-loss garanti après chaque BUY (EM-P1-04 : vérification cycle live active)
- [ ] Paper trading validé 5 jours minimum

---

## TABLEAU DE SUIVI

| ID | Titre | Sévérité | Fichier | Effort | Statut | Date |
|----|-------|----------|---------|--------|--------|------|
| EM-P1-01 | Horodatage BUY/SELL manquant | 🟠 P1 | email_templates.py:170,204 | Faible (1-2h) | ✅ | 2026-03-19 |
| EM-P1-02 | Double préfixage [MULTI_ASSETS][BOT CRYPTO] | 🟠 P1 | email_templates.py | Faible (1h) | ✅ | 2026-03-19 |
| EM-P1-03 | Watchdog restart silencieux | 🟠 P1 | watchdog.py:211 | Moyen (2-3h) | ✅ | 2026-03-19 |
| EM-P1-04 | Position sans SL — vérif cycle live | 🟠 P1 | MULTI_SYMBOLS.py:1180 | Moyen (3-4h) | ✅ | 2026-03-19 |
| EM-P2-01 | Cascade 3 emails save_bot_state | 🟡 P2 | MULTI_SYMBOLS.py:455 | Faible (1h) | ✅ | 2026-03-19 |
| EM-P2-02 | Circuit breaker ouvert sans email | 🟡 P2 | error_handler.py:71 | Faible (1h) | ✅ | 2026-03-19 |
| EM-P2-03 | Breakeven/trailing sans email | 🟡 P2 | order_manager.py | Faible (1-2h) | ✅ | 2026-03-19 |
| EM-P2-04 | Corps inline — migration vers templates | 🟡 P2 | error_handler.py:155 | Moyen (3-4h) | ✅ | 2026-03-19 |
| EM-P2-05 | Drawdown kill-switch absent | 🟡 P2 | MULTI_SYMBOLS.py (nouveau) | Élevé (6-8h) | ✅ | 2026-03-19 |
| EM-P3-01 | Convention sévérité sujets | 🟡 P3 | email_templates.py | Faible (1h) | ✅ | 2026-03-19 |
| EM-P3-02 | args/kwargs bruts dans generic_exception | 🟡 P3 | email_templates.py:254 | Faible (0.5h) | ✅ | 2026-03-19 |

---

## RÉSULTAT FINAL

**Tests :** 685/685 passed (2026-03-19)

**Modifications code :**
- `code/src/email_templates.py` — horodatage, suppression `[BOT CRYPTO]`, convention `[INFO]/[WARN]/[CRIT]`, filtre `_SENSITIVE` (EM-P1-01, EM-P1-02, EM-P3-01, EM-P3-02)
- `code/src/watchdog.py` — `_notify_bot_restarted()` dans `restart_bot()` (EM-P1-03)
- `code/src/MULTI_SYMBOLS.py` — vérif SL cycle live, throttle save_bot_state, alerte drawdown (EM-P1-04, EM-P2-01, EM-P2-05)
- `code/src/error_handler.py` — `handle_error_alert`, notification circuit breaker OUVERT (EM-P2-02, EM-P2-04)
- `code/src/order_manager.py` — emails breakeven/trailing via `deps.send_alert_fn` (EM-P2-03)
