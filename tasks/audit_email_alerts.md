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
