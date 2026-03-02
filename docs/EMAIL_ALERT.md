# Module email_alert.py

Ce module centralise l'envoi des alertes mail pour tout le projet.

## Utilisation

```python
from email_alert import send_email_alert, send_trading_alert_email

# Envoi simple
send_email_alert("Sujet", "Corps du message")

# Envoi avec retry personnalisé
send_email_alert("Sujet", "Corps", max_retries=5, base_delay=3.0)

# Envoi d'alerte trading enrichie
send_trading_alert_email("Sujet", "Corps principal", client=my_client, extra="Texte additionnel")
```

## Configuration SMTP

Les variables d'environnement suivantes doivent être définies :
- `SENDER_EMAIL` : adresse expéditrice
- `SMTP_PASSWORD` : mot de passe SMTP
- `RECEIVER_EMAIL` : destinataire
- `SMTP_SERVER` (optionnel, défaut : smtp.gmail.com)
- `SMTP_PORT` (optionnel, défaut : 587)

## Robustesse
- Gestion automatique des erreurs et logs
- Retry exponentiel sur échec d'envoi
- Centralisation de la logique

## Tests
Voir [tests/test_email_alert.py](../tests/test_email_alert.py) pour des exemples de tests unitaires.
