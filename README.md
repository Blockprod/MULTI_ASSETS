# MULTI_ASSETS — Bot de Trading Crypto Multi-Actifs

Bot de trading algorithmique Binance Spot capable de gérer plusieurs paires
de crypto-monnaies simultanément, avec backtest intégré, gestion du risque
avancée et alertes e-mail en temps réel.

## Architecture

```
code/src/
├── MULTI_SYMBOLS.py      # Point d'entrée — orchestration principale
├── bot_config.py          # Configuration centralisée (.env), décorateurs
├── exchange_client.py     # Client Binance, ordres, filtres symboles
├── position_sizing.py     # Calcul de taille de position (risk, fixed, vol-parity)
├── email_utils.py         # Envoi d'alertes e-mail (SMTP)
├── email_templates.py     # Templates d'e-mails (subject/body)
├── state_manager.py       # Sauvegarde/chargement de l'état du bot (pickle)
├── cache_manager.py       # Cache des données historiques (pickle + lock)
├── indicators.py          # Indicateurs techniques (RSI, MACD, ADX, ATR)
├── error_handler.py       # Circuit-breaker et gestion d'erreurs
├── trade_journal.py       # Journal de trades (JSONL)
├── walk_forward.py        # Walk-forward analysis pour les backtests
├── watchdog.py            # Surveillance santé du bot (heartbeat)
├── preload_data.py        # Pré-chargement des données historiques
└── benchmark.py           # Benchmarking des performances
```

## Prérequis

- **Python 3.11+**
- Compte Binance avec clés API (Lecture + Spot Trading)
- Compte Gmail avec mot de passe d'application (pour les alertes)

## Installation

```bash
# 1. Cloner et aller dans le répertoire
cd MULTI_ASSETS

# 2. Créer un environnement virtuel
python -m venv .venv

# 3. Activer l'environnement
.\.venv\Scripts\Activate.ps1   # PowerShell
# ou
.\.venv\Scripts\activate.bat   # CMD

# 4. Installer les dépendances
pip install -r requirements.txt

# 5. Configurer les variables d'environnement
copy .env.example .env
# Puis éditer .env avec vos clés API et identifiants e-mail
```

## Configuration

Toute la configuration passe par le fichier `.env` (voir `.env.example`).

### Variables requises

| Variable              | Description                      |
|-----------------------|----------------------------------|
| `BINANCE_API_KEY`     | Clé API Binance                  |
| `BINANCE_SECRET_KEY`  | Clé secrète Binance              |
| `SENDER_EMAIL`        | E-mail expéditeur (Gmail)        |
| `RECEIVER_EMAIL`      | E-mail destinataire des alertes  |
| `GOOGLE_MAIL_PASSWORD`| Mot de passe d'application Gmail |

### Variables optionnelles

Les valeurs par défaut conviennent pour un usage standard. Voir `.env.example`
pour la liste complète (frais, slippage, modes de sizing, seuils ATR, etc.).

## Lancement

```bash
# Mode standard (backtest + trading live)
cd code/src
python MULTI_SYMBOLS.py
```

### En production avec PM2

```bash
pm2 start config/ecosystem.config.js
pm2 save
```

## Tests

```bash
# Lancer tous les tests
python -m pytest tests/ -v

# Lancer un fichier de tests spécifique
python -m pytest tests/test_core.py -v
```

78 tests couvrent : configuration, sizing, backtest, error handling, journal
de trades, alertes e-mail, indicateurs et watchdog.

## Fonctionnalités

- **Multi-paires** : Trading simultané sur plusieurs paires (BTC, ETH, SOL, etc.)
- **Backtest intégré** : Walk-forward analysis avec métriques détaillées
- **Gestion du risque** : 4 modes de sizing (baseline, risk, fixed_notional, volatility_parity)
- **Sorties partielles** : Prise de profit progressive à 2 seuils configurables
- **Trailing stop** : Activation dynamique basée sur l'ATR
- **Circuit-breaker** : Protection contre les cascades d'erreurs API
- **Alertes e-mail** : Notifications pour trades, erreurs, déconnexions
- **Cache intelligent** : Données historiques mises en cache avec mise à jour incrémentale
- **État persistant** : Reprise automatique après redémarrage

## Licence

Projet privé — usage personnel uniquement.
