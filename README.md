# MULTI_ASSETS_BOT

Bot de trading multi-actifs pour crypto-monnaies.

## Description
Ce projet contient un bot de trading algorithmique capable d'exécuter des stratégies multi-actifs sur différents marchés crypto. Il inclut des modules pour le backtest, l'exécution en temps réel, la gestion des logs, la surveillance et l'automatisation via un service Windows.

## Structure du projet

- `MULTI_SYMBOLS.py` : Script principal du bot (backtest, trading, gestion des signaux)
- `service.log` : Fichier de log principal du bot
- `requirements.txt` : Liste des dépendances Python
- `install_service.bat` : Script d'installation du service Windows via NSSM
- `cache/` : Dossier de cache pour les données de marché
- `states/` : Dossier pour l'état du bot
- `backup/` : Sauvegardes et historiques

## Installation

1. Cloner le dépôt ou copier le dossier sur votre machine.
2. Créer et activer un environnement virtuel Python :
   ```sh
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1  # PowerShell
   # ou
   .\.venv\Scripts\activate.bat  # CMD
   ```
3. Installer les dépendances :
   ```sh
   pip install -r requirements.txt
   ```

## Lancement du bot

- Pour lancer le bot en mode manuel :
  ```sh
  python MULTI_SYMBOLS.py
  ```
- Pour lancer le bot en tant que service Windows, utiliser NSSM (voir ci-dessous).

## Gestion du service Windows

Le bot peut être installé comme service Windows pour tourner en continu, même sans session ouverte.

### Installation du service

Utiliser le script `install_service.bat` ou configurer manuellement avec NSSM :

- Chemin de l'exécutable : `C:\Users\averr\BIBOT\.venv\Scripts\python.exe`
- Script à exécuter : `C:\Users\averr\BIBOT\MULTI_ASSETS_BOT\code\MULTI_SYMBOLS.py`
- Répertoire de démarrage : `C:\Users\averr\BIBOT\MULTI_ASSETS_BOT\code`

### Tips

Pour éditer la configuration du service Windows lié au bot :

```sh
nssm edit CryptoBot_MultiAssets
```

## Logs

- Les logs d'exécution se trouvent dans `service.log`.
- Les erreurs sont dans `service_error.log`.

## Dépannage

- Vérifiez que le service utilise le bon script et le bon environnement Python.
- Synchronisez les dépendances avec `requirements.txt`.
- Consultez les logs pour tout message d'erreur ou d'avertissement.

## Auteur
- averr

## Licence
Ce projet est privé et réservé à un usage personnel ou interne.
