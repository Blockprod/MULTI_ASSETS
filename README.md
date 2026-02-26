# Binance Trading Bot V2

## Description
Bot de trading modulaire pour Binance, conçu pour la robustesse, la maintenabilité et la sécurité. Architecture professionnelle, tests unitaires, configuration flexible, logs structurés, et mode démo/production automatique.

## Installation
1. **Cloner le dépôt**
2. **Créer un environnement virtuel Python**
   ```sh
   python -m venv .venv
   .venv\Scripts\activate  # Windows
   source .venv/bin/activate  # Linux/Mac
   ```
3. **Installer les dépendances**
   ```sh
   pip install -r trading_bot/requirements.txt
   ```

## Configuration
- Copier `.env.example` en `.env` à la racine du projet.
- Renseigner vos vraies clés API Binance et emails pour le mode production.
- Si aucune variable d’environnement n’est définie, le bot démarre en mode démo (aucun ordre réel).

## Lancement
```sh
python trading_bot/main.py
```
- Le mode (démo/production) est affiché au démarrage.
- Le scheduler exécute une tâche toutes les minutes (affichage du solde USDC, extensible).

## Tests unitaires
```sh
pytest trading_bot/tests --maxfail=5 --disable-warnings -v
```

## Structure du projet
- `core/` : configuration, client Binance, gestion d’état
- `data/` : fetcher, indicateurs
- `strategy/` : backtest, signaux
- `trading/` : gestion des ordres
- `utils/` : décorateurs, affichage, email
- `tests/` : tests unitaires pour chaque module

## Sécurité
- Ne jamais commit vos vraies clés API.
- Le mode démo protège contre toute action réelle par défaut.

## Personnalisation
- Ajouter vos stratégies dans `strategy/`
- Étendre les tâches périodiques dans `main.py`
- Ajouter des logs, alertes, ou intégrations selon vos besoins

---

**Auteur :** Refactoring & architecture par GitHub Copilot (GPT-4.1)
