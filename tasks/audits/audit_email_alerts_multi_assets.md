# Audit — Système d alertes email MULTI_ASSETS
> Date : 2026-03-19
> Perimetre : email_utils.py, email_templates.py, error_handler.py, order_manager.py, backtest_orchestrator.py, MULTI_SYMBOLS.py (hooks email), watchdog.py, bot_config.py

---

## BLOC 1 — SYSTEME D ENVOI

| Point | Statut | Fichier:Ligne | Detail |
|-------|--------|--------------|--------|
| Retry avec backoff sur echec SMTP | COUVERT | email_utils.py:19 | @retry_with_backoff(max_retries=3, base_delay=2.0) sur send_email_alert |
| Cooldown entre alertes similaires | COUVERT | error_handler.py:26,30 + backtest_orchestrator.py:136 | _EMAIL_COOLDOWN_SECONDS=300 (error_handler), backtest_throttle_seconds=3600 (OOS), backtest_throttle_seconds=3600 (daily_loss) |
| Transport SMTP TLS port 587 | COUVERT | bot_config.py:53-54 + email_utils.py:34-37 | smtp_port=587, server.starttls() explicitement appele |
| Echec envoi : logge sans crasher | COUVERT | email_utils.py:43-44 | logger.error puis raise capture par le decorateur retry. Tous les appelants wrappent dans try/except |
| GOOGLE_MAIL_PASSWORD depuis env | COUVERT | bot_config.py:137 | smtp_password = GOOGLE_MAIL_PASSWORD -> os.environ uniquement dans Config.from_env() |

**Point d attention :** send_email_alert() prefixe le sujet avec [{project}] (email_utils.py:30). Les templates email_templates.py ont deja [BOT CRYPTO]. Resultat : sujets double-prefixes [MULTI_ASSETS] [BOT CRYPTO] Achat execute. Voir EM-P1-02.

---

## BLOC 2 — COUVERTURE DES EVENEMENTS

### Evenements systeme

| Evenement | Statut | Fichier:Ligne | Detail |
|-----------|--------|--------------|--------|
| Exception critique non geree | COUVERT | error_handler.py:202 + MULTI_SYMBOLS.py:1589 | error_handler.handle_error(critical=True) -> send_alert_email() + critical_startup_error_email au demarrage |
| Echec save_state 3x -> emergency_halt | COUVERT | MULTI_SYMBOLS.py:455-468 | Email a chaque echec (_save_failure_count/3) avec avertissement progressif. set_emergency_halt() active apres le 3eme. |
| Echec connexion API Binance | COUVERT | MULTI_SYMBOLS.py:407 | validate_api_connection() -> api_connection_failure_email (template centralise) |
| Donnees OHLCV manquantes/corrompues | COUVERT | MULTI_SYMBOLS.py:700,706 | data_retrieval_error_email + network_error_email injectes dans fetch_historical_data() |
| Circuit breaker declenche | A VERIFIER | error_handler.py:202 | Email par erreur individuelle (contexte fourni), mais pas de notification dediee CIRCUIT OUVERT quand le seuil de 3 echecs est atteint. L operateur ne sait pas que le bot est en mode PAUSED. |
| Watchdog : bot considere comme hung | A VERIFIER | watchdog.py:211-213 | Redemarrage sur heartbeat_stale (>10 min) : SILENCIEUX -- juste log WARNING. Email uniquement si le watchdog abandonne definitivement via _notify_watchdog_stopped. Un restart normal passe inapercu. |

### Evenements trading

| Evenement | Statut | Fichier:Ligne | Detail |
|-----------|--------|--------------|--------|
| BUY execute (paire, qty, prix, stop) | COUVERT | order_manager.py:1325 | buy_executed_email(pair, qty, price, usdc_spent, usdc_after, extra=scenario+SL+risque). Manque horodatage (voir EM-P1-01). |
| SELL execute (paire, raison, PnL) | COUVERT | order_manager.py:389,573,718,984 | sell_executed_email sur 4 chemins : signal, SL fixe, trailing stop, partielle. |
| Ordre bloque (raison explicite) | COUVERT | order_manager.py:352 + backtest_orchestrator.py:299 | Vente partielle bloquee (min_qty/min_notional) + backtest sans resultat valide. |
| Ordre echoue (timeout, rejet Binance) | COUVERT | exchange_client.py -> send_alert callback | order_error_email et order_exception_email via callback injecte |
| Stop-loss declenche | COUVERT | order_manager.py:573 | sell_executed_email avec sell_reason=STOP-LOSS (fixe 3xATR) ou TRAILING-STOP. Inclut prix entree, stop fixe, PnL%. |
| Vente partielle executee | COUVERT | order_manager.py:389 | sell_executed_email avec description Prise de profit partielle 1/2. Non-filled aussi alerte (order_manager.py:455). |
| Position ouverte sans stop-loss | A VERIFIER | MULTI_SYMBOLS.py:1180 | _verify_all_stops_on_shutdown() verifie les stops lors de l arret uniquement (SIGTERM, atexit). Aucune verification temps reel pendant les cycles de 2 min. |

### Evenements protection capital

| Evenement | Statut | Fichier:Ligne | Detail |
|-----------|--------|--------------|--------|
| daily_loss_limit atteint | COUVERT | MULTI_SYMBOLS.py:578 | Email avec cooldown 1h. Inclut montant perdu vs limite en USDC et %. |
| Drawdown kill-switch declenche | NON COUVERT | -- | Aucun kill-switch drawdown distinct dans le code. emergency_halt couvre le cas general mais sans notification dediee drawdown max. |
| oos_blocked active | COUVERT | backtest_orchestrator.py:140 | Email avec cooldown 1h, indique les criteres non atteints (Sharpe, WinRate). |
| emergency_halt active | COUVERT | MULTI_SYMBOLS.py:455 + order_manager.py:1427 | Email envoye avant l activation dans les deux chemins (P0-SAVE et P0-STOP double echec). |

---

## BLOC 3 — QUALITE DU CONTENU

| Critere | Statut | Fichier:Ligne | Detail |
|---------|--------|--------------|--------|
| Paire + prix + qty + raison | COUVERT | email_templates.py:170,204 | buy_executed_email et sell_executed_email incluent tous ces champs |
| Horodatage dans emails trading | NON COUVERT | email_templates.py:170,204 | buy_executed_email et sell_executed_email n incluent pas de timestamp. Templates reseau ont _timestamp(). Incoherence. |
| Emails erreur incluent traceback | COUVERT | email_templates.py:228,241 | trading_execution_error_email et trading_pair_error_email incluent traceback_str[:500]. error_handler inclut le champ traceback dans error_details. |
| Credential Binance dans le corps | A VERIFIER | email_templates.py:254 | generic_exception_email inclut args et kwargs bruts. Si une fonction recevant des credentials est decoree @log_exceptions, ceux-ci pourraient apparaitre dans l email. |
| Sujets distinguent critique vs informatif | A VERIFIER | multiple | Convention non systematique : melange [BOT CRYPTO], [ALERTE BOT CRYPTO], [CRITIQUE], [EMERGENCY HALT], [BOT ALERT]. Filtrage cote recepteur difficile. |
| Template email centralise | COUVERT (partiel) | email_templates.py | ~70% des templates centralises. Exception : corps inline dans error_handler.py:155 et ~30% des alertes order_manager.py/backtest_orchestrator.py. |

---

## BLOC 4 — CAS MANQUANTS ET RISQUES

### Erreurs critiques swallowees sans notification

| Cas | Statut | Fichier:Ligne |
|-----|--------|--------------|
| Echec annulation SL avant vente signal | Email envoye | order_manager.py:96 |
| Restart watchdog sur heartbeat stale | SILENCIEUX | watchdog.py:211 |
| load_bot_state() HMAC mismatch | Email (docstring C-04) | MULTI_SYMBOLS.py:473 |
| _execute_live_trading_only() exception | Email envoye | backtest_orchestrator.py:427 |
| Breakeven active | Log only | order_manager.py (trailing logic) |
| Trailing stop active | Log only | order_manager.py (trailing logic) |

### Cascade d emails identiques sur retry loop

| Cas | Risque | Mitigation |
|-----|--------|-----------|
| save_bot_state() 3 echecs consecutifs | 3 emails en ~5s | Aucun throttle sur ce chemin (send_trading_alert_email bypass le throttle error_handler) |
| error_handler.send_alert_email() | Throttle 300s | _EMAIL_COOLDOWN_SECONDS + lock |
| OOS alert | Throttle 3600s | oos_alert_last_sent + lock |
| Daily loss | Throttle 3600s | _daily_loss_alert_last_sent + lock |

### Bot continue si SMTP echoue

COUVERT -- tous les appelants wrappent send_email_alert() dans try/except.
Le @retry_with_backoff(max_retries=3) absorbe les pannes transitoires SMTP.
Apres 3 echecs definitifs, l exception est capturee par le try/except parent et loggee.

---

## SYNTHESE

### Tableau des problemes identifies

| ID | Bloc | Description | Fichier:Ligne | Severite | Impact | Effort |
|----|------|-------------|--------------|----------|--------|--------|
| EM-P1-01 | BLOC 3 | buy_executed_email/sell_executed_email sans horodatage | email_templates.py:170,204 | P1 | Diagnostic post-incident difficile | Faible -- ajouter _timestamp() |
| EM-P1-02 | BLOC 1 | Double prefixage des sujets [MULTI_ASSETS] [BOT CRYPTO] | email_utils.py:30 + email_templates.py | P1 | Filtres email operateur casses | Faible -- supprimer [BOT CRYPTO] des templates |
| EM-P1-03 | BLOC 2 | Watchdog restart silencieux sur heartbeat stale | watchdog.py:211 | P1 | Crash/freeze du bot invisible jusqu a abandon watchdog | Moyen -- ajouter _send_email_alert() dans restart_bot() |
| EM-P1-04 | BLOC 2 | Position BUY sans SL verifiee seulement au shutdown | MULTI_SYMBOLS.py:1180 | P1 | Fenetre de risque non detectee entre les cycles de 2 min | Moyen -- ajouter verification sl_exchange_placed dans cycle live |
| EM-P2-01 | BLOC 4 | 3 emails en cascade sur save_bot_state() failures | MULTI_SYMBOLS.py:455 | P2 | Spam operateur en cas de probleme disque | Faible -- throttle ou 1 email groupe |
| EM-P2-02 | BLOC 2 | Circuit breaker OUVERT sans notification dediee | error_handler.py:71 | P2 | Operateur ne sait pas que le bot est en mode PAUSED | Faible -- email dans record_failure() quand is_open=True |
| EM-P2-03 | BLOC 4 | Breakeven et trailing activation sans email | order_manager.py | P2 | Sans visibilite sur les modifications de protection | Faible -- ajouter send_alert_fn() au declenchement |
| EM-P2-04 | BLOC 3 | Corps inline dans ~30% des alertes (non-templates) | error_handler.py:155 | P2 | Duplication de logique, maintenance difficile | Moyen -- migrer vers email_templates.py |
| EM-P2-05 | BLOC 2 | Drawdown kill-switch inexistant | -- | P2 | Aucune alerte si paire perd X% sur valeur depuis entree | Eleve -- feature manquante |
| EM-P3-01 | BLOC 3 | Convention de severite non systematique dans les sujets | email_templates.py | P3 | Filtres regles email impossibles a normaliser | Faible -- convention [INFO]/[WARN]/[CRIT] |
| EM-P3-02 | BLOC 3 | generic_exception_email expose args/kwargs bruts | email_templates.py:254 | P3 | Risque faible de fuite credentials | Faible -- masquer ou tronquer args |

### Evenements NON COUVERTS par criticite

**Important (P1)**
- Watchdog restart sur heartbeat stale -> silencieux
- Position BUY sans SL detectee seulement au shutdown
- Emails de trading BUY/SELL sans horodatage

**Mineur (P2)**
- Breakeven active -> log only
- Trailing activation -> log only
- Circuit breaker OUVERT -> pas de notification dediee
- Drawdown kill-switch -> inexistant

### Top 3 risques lies aux alertes manquantes

1. **Watchdog silencieux** (EM-P1-03) : si le bot crashe et redemarre plusieurs fois par heure, l operateur ne le sait pas. Le bot peut rater des cycles de trading complets (1h+) sans aucune notification. Risque : position non geree si le bot ne revient pas a l etat attendu.

2. **Position sans SL non detectee en live** (EM-P1-04) : si un ordre SL exchange est supprime sur Binance entre deux cycles de 2 min, le bot ne l alerte pas jusqu au prochain shutdown. Risque : exposition capital reel sans protection.

3. **Spam sur save_state failures** (EM-P2-01) + **double prefixage** (EM-P1-02) : alerte critique noyee dans du spam ou dont le sujet est mal forme. Risque operationnel indirect.

### Points forts a conserver

- COUVERT : Retry SMTP robuste -- @retry_with_backoff(max_retries=3) + failover silencieux
- COUVERT : Templates centralises email_templates.py -- ~70% des emails (maintenable)
- COUVERT : Cooldown anti-spam -- OOS (1h), daily_loss (1h), error_handler (5 min)
- COUVERT : Credentials masques -- Config.__repr__ + aucun log direct api_key/secret_key
- COUVERT : Couverture BUY/SELL complete -- 4 chemins de vente avec contexte riche (scenario, EMA, PnL%)
- COUVERT : Email critique bypass throttle -- error_handler.send_alert_email(critical=True) ignore le cooldown
