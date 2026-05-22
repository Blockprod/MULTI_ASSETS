# MULTI_ASSETS — Bot de Trading Algorithmique Multi-Actifs

Plateforme de trading algorithmique gérant deux bots indépendants :
- **Bot Binance Spot** — multi-paires crypto (BTC, SOL, PEPE…) en USDC
- **Bot IBKR Forex** — EUR/USD et GBP/USD via IB Gateway (paper / live)

Les deux bots partagent le même moteur de backtest Cython, le walk-forward
validation OOS et les modules d'indicateurs.

## Architecture

```
code/src/
├── MULTI_SYMBOLS.py       # Bot Binance — orchestration principale (multi-paires)
├── bot_config.py           # Configuration centralisée (.env)
├── exchange_client.py      # Client Binance (ordres, filtres, rate limiter)
├── backtest_runner.py      # Moteur backtest Python + wrapper Cython
├── backtest_orchestrator.py# Sélection IS/OOS et déclenchement backtests
├── walk_forward.py         # Walk-forward validation (OOS gates Sharpe/WR/decay)
├── position_sizing.py      # Sizing (risk, fixed, vol-parity)
├── order_manager.py        # BUY/SELL, stop-loss natif, partial sells
├── state_manager.py        # État JSON_V1 + HMAC-SHA256
├── indicators_engine.py    # EMA adaptatif, cache indicateurs
├── email_utils.py          # Alertes e-mail SMTP
├── watchdog.py             # Surveillance heartbeat (continuité de service)
├── cython_integrity.py     # Vérification SHA256 des .pyd au démarrage
└── ibkr/                   # Bot IBKR Forex
    ├── IBKR_FOREX.py       # Orchestrateur principal Forex
    ├── ibkr_config.py      # Config IBKR (levier, risque, connexion)
    ├── ibkr_client.py      # Client IB Gateway (ib_insync)
    ├── ibkr_order_manager_forex.py  # Ordres Forex + stop-loss ATR
    ├── ibkr_data_fetcher.py         # OHLCV IBKR + cache
    └── ibkr_state_manager.py        # État JSON_V1 + HMAC-SHA256

code/bin/
├── backtest_engine_standard.cp311-win_amd64.pyd  # Moteur backtest Cython
└── indicators.cp311-win_amd64.pyd                # Indicateurs Cython
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

### Variables requises — Bot Binance

| Variable              | Description                      |
|-----------------------|----------------------------------|
| `BINANCE_API_KEY`     | Clé API Binance                  |
| `BINANCE_SECRET_KEY`  | Clé secrète Binance              |
| `SENDER_EMAIL`        | E-mail expéditeur (Gmail)        |
| `RECEIVER_EMAIL`      | E-mail destinataire des alertes  |
| `GOOGLE_MAIL_PASSWORD`| Mot de passe d'application Gmail |

### Variables requises — Bot IBKR Forex

| Variable           | Défaut  | Description                                    |
|--------------------|---------|------------------------------------------------|
| `IBKR_SECRET`      | —       | Clé HMAC pour signature de l'état persisté     |
| `IBKR_CLIENT_ID`   | `4`     | clientId IB Gateway (≠ AlphaEdge = 3)          |
| `IBKR_PAPER_MODE`  | `true`  | `false` pour passer en trading réel            |
| `IBKR_MAX_LEVERAGE`| `5`     | Levier maximum (5x recommandé pour Forex)      |
| `IBKR_RISK_PER_TRADE`| `0.02`| Risque par trade (2% du capital)               |

### Variables optionnelles

Les valeurs par défaut conviennent pour un usage standard. Voir `.env.example`
pour la liste complète (frais, slippage, modes de sizing, seuils ATR, etc.).

## Lancement

```bash
# Mode standard (backtest + trading live)
cd code/src
python MULTI_SYMBOLS.py
```

### En production avec le watchdog

```powershell
# Bot Binance
.\start_safe.ps1

# Bot IBKR Forex
python -m code.src.ibkr.IBKR_FOREX
```

Le watchdog (`watchdog.py`) assure la continuité de service en surveillant le heartbeat
et en redémarrant le bot Binance en cas de crash.

## Tests

```bash
# Lancer tous les tests
python -m pytest tests/ -v

# Lancer un fichier de tests spécifique
python -m pytest tests/test_core.py -v
```

836 tests couvrent : configuration, sizing, backtest Cython + Python fallback,
error handling, alertes e-mail, indicateurs, walk-forward OOS, intégrité Cython,
client IBKR et data fetcher Forex.

## Modes d'exécution

| Mode | Commande | Effet |
|------|----------|-------|
| **Backtest seul** | `python code/src/backtest_runner.py` | Aucun ordre réel, aucune connexion requise |
| **Binance Live** | `.\start_safe.ps1` | Ordres réels Binance Spot — clés API actives |
| **IBKR Paper** | `python -m code.src.ibkr.IBKR_FOREX` | Paper trading IB Gateway port 4002 |
| **IBKR Live** | `IBKR_PAPER_MODE=false python -m code.src.ibkr.IBKR_FOREX` | Ordres réels Forex via IB Gateway port 4001 |

> ⚠️ En mode Live, toute position ouverte engage du capital réel.  
> Vérifier les clés API et `IBKR_PAPER_MODE=false` dans `.env` avant tout démarrage.

## Ressources AI & Architecture

| Fichier | Rôle |
|---------|------|
| `.github/copilot-instructions.md` | Contexte principal pour GitHub Copilot |
| `.claude/context.md` | Contexte principal pour Claude |
| `.claude/rules.md` | Règles de modification + priorités |
| `agents/code_auditor.md` | Agent spécialisé audit sécurité/concurrence |
| `agents/quant_engineer.md` | Agent spécialisé backtest/signaux |
| `agents/risk_manager.md` | Agent spécialisé gestion du risque |
| `architecture/decisions.md` | Décisions d'architecture documentées |
| `architecture/system_design.md` | Design système global |

## Build Cython

Les modules Cython compilés (`.pyd` / `.so`) ne sont **pas commités** (`.gitignore`).
Après un `git clone` ou toute modification d'un fichier `.pyx`, recompiler via :

```powershell
# Depuis la racine du repo
.venv\Scripts\python.exe config/setup.py build_ext --inplace

# Déplacer les compilés générés dans code/ vers code/bin/
Move-Item code\indicators*.pyd code\bin\
Move-Item code\backtest_engine_standard*.pyd code\bin\
```

**Modules Cython actifs** (dans `code/bin/`) :

| Module | Rôle |
|--------|------|
| `backtest_engine_standard` | Moteur de backtest principal (params runtime, risk sizing) |
| `indicators` | Calcul des indicateurs techniques (EMA, RSI, ATR, StochRSI…) |

> Le module `backtest_engine` (legacy) est archivé dans `code/legacy/` — ne pas réactiver
> sans migration complète vers des paramètres runtime (voir `code/legacy/backtest_engine.pyx`).

## Fonctionnalités

- **Multi-paires** : Trading simultané sur plusieurs paires (BTC, ETH, SOL, etc.)
- **Backtest intégré** : Walk-forward analysis avec métriques détaillées
- **Gestion du risque** : 4 modes de sizing (baseline, risk, fixed_notional, volatility_parity)
- **Sorties partielles** : Prise de profit progressive à 2 seuils configurables
- **Stop-loss natif** : `STOP_LOSS_LIMIT` exchange-natif posé immédiatement après chaque BUY (le `TRAILING_STOP_MARKET` n'existe pas sur Binance Spot)
- **Circuit-breaker** : Protection contre les cascades d'erreurs API
- **Alertes e-mail** : Notifications pour trades, erreurs, déconnexions
- **Cache intelligent** : Données historiques mises en cache avec mise à jour incrémentale
- **État persistant** : Reprise automatique après redémarrage

## Licence

Projet privé — usage personnel uniquement.
