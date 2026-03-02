# PLAN D'ACTION — MULTI_ASSETS Trading Bot

**Basé sur** : [AUDIT_COMPLET.md](AUDIT_COMPLET.md)  
**Date** : Mars 2026  
**Objectif** : Faire passer le bot de **4/10** (fonctionnel mais fragile) à **8/10** (robuste et maintenable)

---

## VISION GLOBALE

```
PHASE 0 ──── URGENCES SÉCURITÉ & FINANCIER ────── Jour 1         (1h)     ██░░░░░░░░
PHASE 1 ──── FIABILITÉ DU TRADING LIVE ────────── Jours 2-3      (6h)     ████░░░░░░
PHASE 2 ──── ACTIVATION DES MODULES DORMANTS ──── Jours 4-6      (8h)     ██████░░░░
PHASE 3 ──── COUVERTURE DE TESTS ──────────────── Jours 7-10     (10h)    ████████░░
PHASE 4 ──── REFACTORING ARCHITECTURAL ────────── Semaines 3-4    (20h)    ██████████
PHASE 5 ──── DURCISSEMENT & DOCUMENTATION ─────── Semaine 5       (5h)    ██████████
```

**Effort total estimé** : ~50 heures de développement  
**Principe directeur** : Chaque phase est déployable indépendamment. Aucune phase ne casse le fonctionnement existant.

---

## PHASE 0 — URGENCES SÉCURITÉ & FINANCIER

> **Quand** : Jour 1 — immédiatement  
> **Durée** : ~1 heure  
> **Risque si non fait** : Fuite de clé API, ordres rejetés, perte financière directe  
> **Prérequis** : Aucun  
> **Fichier impacté** : `code/src/MULTI_SYMBOLS.py`

### 0.1 — Supprimer la fuite de clé API dans les logs

| | |
|---|---|
| **Réf. audit** | SEC-01 |
| **Effort** | 5 min |
| **Quoi** | Supprimer ou masquer la ligne `logger.error(f"[DEBUG ORDER] Headers envoyés: {headers}")` (~L1033) |
| **Comment** | Remplacer par `logger.debug("[DEBUG ORDER] Headers: API_KEY=***MASKED***")` |
| **Validation** | Grep «X-MBX-APIKEY» dans les fichiers de log → 0 résultat |

### 0.2 — Corriger le timestamp sur les 3 fonctions REST brutes

| | |
|---|---|
| **Réf. audit** | CRIT-01 |
| **Effort** | 15 min |
| **Quoi** | Dans `_direct_market_order`, `place_stop_loss_order`, `place_trailing_stop_order` : appliquer l'offset |
| **Comment** | Remplacer `timestamp = int(time.time() * 1000)` par `timestamp = int(time.time() * 1000) + self._server_time_offset` dans chaque fonction |
| **Validation** | Exécuter un ordre test → pas d'erreur `-1021` |

### 0.3 — Supprimer le logger + imports dupliqués à mi-fichier

| | |
|---|---|
| **Réf. audit** | CRIT-07 |
| **Effort** | 5 min |
| **Quoi** | Supprimer le bloc `import time / import logging / logger = logging.getLogger(__name__)` à ~L3430-3435 |
| **Comment** | Supprimer ces 4 lignes (les imports sont déjà en haut du fichier) |
| **Validation** | `grep -n "logger = logging.getLogger" MULTI_SYMBOLS.py` → 1 seul résultat (en haut) |

### 0.4 — Remplacer le DummyErrorHandler par le vrai ErrorHandler

| | |
|---|---|
| **Réf. audit** | FIAB-07 |
| **Effort** | 30 min |
| **Quoi** | Le `__main__` utilise un `DummyErrorHandler` (L72) qui ignore toutes les erreurs au lieu du vrai `ErrorHandler` de `error_handler.py` |
| **Comment** | 1. Supprimer la classe `DummyErrorHandler` et la fonction locale `initialize_error_handler()` du fichier principal. 2. Importer depuis `error_handler.py` : `from error_handler import initialize_error_handler, get_error_handler` |
| **Validation** | Lancer le bot → les logs affichent `[HANDLER] Error handler initialized` (pas `DummyErrorHandler`) |

### Checklist Phase 0

- [ ] 0.1 — Fuite API supprimée
- [ ] 0.2 — Timestamp offset appliqué (3 fonctions)
- [ ] 0.3 — Logger dupliqué supprimé
- [ ] 0.4 — Vrai ErrorHandler activé
- [ ] **Redémarrage du bot et vérification logs sur 1 cycle complet**

---

## PHASE 1 — FIABILITÉ DU TRADING LIVE

> **Quand** : Jours 2-3  
> **Durée** : ~6 heures  
> **Risque si non fait** : Comportements imprévisibles, positions bloquées, config inutile  
> **Prérequis** : Phase 0 terminée  
> **Fichier principal** : `code/src/MULTI_SYMBOLS.py`

### 1.1 — Centraliser les constantes stratégiques dans Config

| | |
|---|---|
| **Réf. audit** | CRIT-02, CRIT-03 |
| **Effort** | 2h |
| **Quoi** | Les ATR multipliers (5.5, 3.0), seuils partiels (+2%, +4%, 50%, 30%), trailing activation (+3%) sont hardcodés à plusieurs endroits |
| **Comment** | 1. Ajouter dans la classe `Config` : `partial_threshold_1`, `partial_threshold_2`, `partial_pct_1`, `partial_pct_2`, `trailing_activation_pct`. 2. Remplacer chaque valeur hardcodée par `config.xxx`. 3. Décider : soit utiliser `config.atr_multiplier` partout (permettre l'évolution), soit renommer la constante hardcodée en `CYTHON_ATR_MULTIPLIER` avec un commentaire explicite |
| **Validation** | Grep `5.5` et `3.0` et `0.02` et `0.04` dans le fichier → uniquement dans `Config.__init__` |

### 1.2 — Vérifier `partial_enabled` avant d'exécuter les partiels

| | |
|---|---|
| **Réf. audit** | CRIT-05 |
| **Effort** | 30 min |
| **Quoi** | `can_execute_partial_safely()` calcule un flag `partial_enabled` après l'achat, mais la logique de vente ne le vérifie jamais |
| **Comment** | Ajouter `if not pair_state.get('partial_enabled', True):` avant chaque bloc `if not pair_state.get('partial_taken_1', False):` dans `execute_real_trades` |
| **Validation** | Test avec une petite position → les partiels sont skippés avec log `[PARTIAL-CHECK] PARTIALS DÉSACTIVÉS` |

### 1.3 — Protéger contre la boucle email récursive

| | |
|---|---|
| **Réf. audit** | CRIT-06 |
| **Effort** | 30 min |
| **Quoi** | `@log_exceptions` → `send_trading_alert_email` → possible re-exception → boucle infinie |
| **Comment** | Ajouter un flag thread-local `_sending_alert = False` dans le décorateur `@log_exceptions`. Si déjà `True`, logger l'erreur sans envoyer d'email. Alternative : wrap le `send_trading_alert_email` dans un `try/except` silencieux dans le décorateur |
| **Validation** | Simuler une erreur SMTP → pas de stack overflow ni de récursion infinie |

### 1.4 — Ajouter les gardes OS pour les commandes Windows-only

| | |
|---|---|
| **Réf. audit** | FIAB-01, FIAB-02, FIAB-03 |
| **Effort** | 1h |
| **Quoi** | `check_network_connectivity`, `sync_windows_silently`, `check_admin_privileges` utilisent des commandes Windows sans vérification |
| **Comment** | Encapsuler chaque appel Windows dans `if os.name == 'nt':` avec un fallback Linux/macOS. Par exemple : `ipconfig` → `ip addr` ou `ifconfig`, `w32tm` → `ntpdate` ou skip |
| **Validation** | Pas de `FileNotFoundError` si exécuté sur Linux (test optionnel) |

### 1.5 — Implémenter les 4 sizing modes dans le backtest Python fallback

| | |
|---|---|
| **Réf. audit** | CRIT-04 |
| **Effort** | 2h |
| **Quoi** | `backtest_from_dataframe()` ne gère que `baseline`. Les modes `risk`, `fixed_notional`, `volatility_parity` tombent en fallback |
| **Comment** | Porter la logique de sizing de `execute_real_trades` (L4050-4100) dans `backtest_from_dataframe`. Utiliser les mêmes fonctions `compute_position_size_by_risk`, `compute_position_size_fixed_notional`, `compute_position_size_volatility_parity` |
| **Validation** | `backtest_from_dataframe(df, ..., sizing_mode='risk')` renvoie des résultats différents de `baseline` |

### Checklist Phase 1

- [ ] 1.1 — Constantes centralisées dans Config
- [ ] 1.2 — Flag `partial_enabled` vérifié
- [ ] 1.3 — Protection boucle email
- [ ] 1.4 — Gardes OS ajoutées
- [ ] 1.5 — Sizing modes en backtest
- [ ] **Test complet : 1 cycle backtest + 1 cycle trading réel avec logs vérifiés**

---

## PHASE 2 — ACTIVATION DES MODULES DORMANTS

> **Quand** : Jours 4-6  
> **Durée** : ~8 heures  
> **Risque si non fait** : Overfitting, pas de traçabilité, watchdog inopérant  
> **Prérequis** : Phase 0 terminée (Phase 1 en parallèle possible)  
> **Fichiers impactés** : `MULTI_SYMBOLS.py`, modules support

### 2.1 — Intégrer `trade_journal.log_trade()` dans le flux de trading

| | |
|---|---|
| **Réf. audit** | Module 10.2 — code mort |
| **Effort** | 1h30 |
| **Quoi** | Le journal de trades (`trade_journal.py`) est bien écrit mais jamais appelé |
| **Comment** | 1. Importer `from trade_journal import log_trade` en haut du fichier. 2. Après chaque `safe_market_buy` réussi, appeler `log_trade(logs_dir=..., pair=..., side='buy', quantity=..., price=..., ...)`. 3. Après chaque vente (complète, partielle, stop-loss), appeler `log_trade(side='sell', pnl=..., ...)`. 4. Ajouter un appel `journal_summary()` dans l'affichage périodique |
| **Validation** | Après 1 cycle de trading → fichier `trade_journal.jsonl` contient des entrées valides |

### 2.2 — Intégrer `walk_forward.run_walk_forward_validation()` dans le backtest

| | |
|---|---|
| **Réf. audit** | Module 10.3 — code mort |
| **Effort** | 3h |
| **Quoi** | La validation walk-forward est implémentée mais jamais invoquée → risque d'overfitting |
| **Comment** | 1. Importer `from walk_forward import run_walk_forward_validation, compute_risk_metrics`. 2. Dans `backtest_and_display_results`, après `run_all_backtests`, appeler `run_walk_forward_validation(base_dataframes, results, scenarios, backtest_fn)`. 3. Afficher les résultats WF dans un Panel Rich dédié. 4. **Optionnel** : si aucune config ne passe les quality gates OOS, afficher un warning mais utiliser quand même le meilleur résultat (mode dégradé) |
| **Validation** | Les logs affichent `WF Fold 1/4: train[0:X] ... PASS/FAIL` |

### 2.3 — Écrire le heartbeat dans la boucle principale

| | |
|---|---|
| **Réf. audit** | Module 10.4 — partiellement mort |
| **Effort** | 30 min |
| **Quoi** | Le watchdog vérifie `heartbeat.json` mais le bot ne l'écrit jamais |
| **Comment** | Dans la boucle `while True:` du `__main__`, ajouter à chaque itération : |

```python
import json
from datetime import datetime, timezone

heartbeat = {
    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "pid": os.getpid(),
    "circuit_mode": error_handler.circuit_breaker.mode.value,
    "error_count": len(error_handler.error_history),
    "loop_counter": running_counter,
}
hb_path = os.path.join(os.path.dirname(__file__), "states", "heartbeat.json")
tmp_path = hb_path + ".tmp"
with open(tmp_path, "w") as f:
    json.dump(heartbeat, f)
os.replace(tmp_path, hb_path)
```

| | |
|---|---|
| **Validation** | `heartbeat.json` se met à jour toutes les 2 minutes avec un timestamp frais |

### 2.4 — Utiliser les exceptions typées de `exceptions.py`

| | |
|---|---|
| **Réf. audit** | Module 10.5 — code mort |
| **Effort** | 3h |
| **Quoi** | 100% des `except` dans `MULTI_SYMBOLS.py` utilisent `except Exception` générique |
| **Comment** | Remplacement progressif, par priorité. Commencer par les fonctions critiques : |
| | 1. `_direct_market_order` : `except BinanceAPIException` → `raise OrderError(...)` |
| | 2. `safe_market_buy/sell` : attraper `InsufficientFundsError` spécifiquement |
| | 3. `fetch_historical_data` : `raise StaleDataError(...)` si données trop vieilles |
| | 4. `load_bot_state` / `save_bot_state` : `raise StateError(...)` si corruption |
| | 5. Config manquante : `raise ConfigError(...)` au démarrage |
| **Validation** | `grep -c "except Exception" MULTI_SYMBOLS.py` diminue de ~40% |

### Checklist Phase 2

- [ ] 2.1 — Trade journal intégré (log_trade après chaque achat/vente)
- [ ] 2.2 — Walk-forward validation active
- [ ] 2.3 — Heartbeat écrit dans la boucle principale
- [ ] 2.4 — Exceptions typées dans les fonctions critiques
- [ ] **1 cycle complet avec vérification : journal JSONL, heartbeat.json, logs WF**

---

## PHASE 3 — COUVERTURE DE TESTS

> **Quand** : Jours 7-10  
> **Durée** : ~10 heures  
> **Risque si non fait** : Régressions silencieuses à chaque modification  
> **Prérequis** : Phases 0-1 terminées  
> **Fichiers impactés** : `tests/`

### 3.1 — Créer des fixtures mock Binance réutilisables

| | |
|---|---|
| **Effort** | 2h |
| **Quoi** | `conftest.py` ne contient qu'un cleanup de logger. Pas de mock partagé pour le client Binance |
| **Comment** | Créer dans `conftest.py` : |
| | - `@pytest.fixture` `mock_binance_client` : mock de `BinanceFinalClient` avec `get_account()`, `get_exchange_info()`, `get_historical_klines()` |
| | - `@pytest.fixture` `sample_ohlcv_df` : DataFrame OHLCV de 1000 lignes avec données réalistes |
| | - `@pytest.fixture` `sample_config` : instance de `Config` avec valeurs de test |
| **Validation** | Les fixtures sont importables dans tous les fichiers de test |

### 3.2 — Tests unitaires pour le backtest

| | |
|---|---|
| **Effort** | 3h |
| **Quoi** | `backtest_from_dataframe` n'a aucun test |
| **Comment** | Créer `tests/test_backtest.py` avec : |
| | - Test avec données croissantes → profit > 0 |
| | - Test avec données décroissantes → profit < 0 |
| | - Test mode `baseline` vs `risk` → résultats différents |
| | - Test trailing stop se déclenche à entry - ATR * multiplier |
| | - Test partial exits à +2% et +4% |
| | - Test avec DataFrame vide → retour gracieux |
| **Validation** | `pytest tests/test_backtest.py -v` → 6+ tests passent |

### 3.3 — Tests unitaires pour le position sizing

| | |
|---|---|
| **Effort** | 2h |
| **Quoi** | Les 4 fonctions de sizing n'ont aucun test |
| **Comment** | Créer `tests/test_sizing.py` avec : |
| | - `compute_position_size_by_risk` : vérifier que risk_pct=1% limite la perte max |
| | - `compute_position_size_fixed_notional` : vérifier le montant en coins |
| | - `compute_position_size_volatility_parity` : vérifier avec ATR connu |
| | - Test edge case : ATR = 0 → fallback baseline |
| | - Test edge case : equity = 0 → quantité = 0 |
| **Validation** | `pytest tests/test_sizing.py -v` → 5+ tests passent |

### 3.4 — Tests pour error_handler.py

| | |
|---|---|
| **Effort** | 1h30 |
| **Quoi** | `error_handler.py` a 0% de couverture |
| **Comment** | Créer `tests/test_error_handler.py` avec : |
| | - CircuitBreaker : 3 failures → circuit ouvert |
| | - CircuitBreaker : timeout expiré → circuit se referme |
| | - ErrorHandler : `handle_error` avec mock email |
| | - ErrorHandler : `safe_execute` avec fonction qui échoue → fallback appelé |
| | - Vérifier que `error_history` se remplit correctement |
| **Validation** | `pytest tests/test_error_handler.py -v` → 5+ tests passent |

### 3.5 — Tests pour trade_journal.py

| | |
|---|---|
| **Effort** | 1h30 |
| **Quoi** | `trade_journal.py` a 0% de couverture |
| **Comment** | Créer `tests/test_trade_journal.py` avec : |
| | - `log_trade` écrit une ligne JSONL valide |
| | - `read_journal` relit les entrées correctement |
| | - `journal_summary` calcule win_rate, total_pnl |
| | - `log_trade` avec `last_n` paramètre |
| | - Test concurrent : 2 threads écrivent en même temps → pas de corruption |
| **Validation** | `pytest tests/test_trade_journal.py -v` → 5+ tests passent |

### Checklist Phase 3

- [ ] 3.1 — Fixtures mock Binance dans conftest.py
- [ ] 3.2 — Tests backtest (6+ tests)
- [ ] 3.3 — Tests sizing (5+ tests)
- [ ] 3.4 — Tests error_handler (5+ tests)
- [ ] 3.5 — Tests trade_journal (5+ tests)
- [ ] **`pytest tests/ -v` → 40+ tests passent, 0 échecs**

---

## PHASE 4 — REFACTORING ARCHITECTURAL

> **Quand** : Semaines 3-4  
> **Durée** : ~20 heures  
> **Risque si non fait** : Maintenabilité faible, modifications risquées  
> **Prérequis** : Phase 3 terminée (tests en place pour détecter les régressions)  
> **Fichiers impactés** : refonte complète de `code/src/`

### 4.1 — Scinder MULTI_SYMBOLS.py en modules

| | |
|---|---|
| **Effort** | 8h |
| **Quoi** | Le fichier de 5 001 lignes contient 10+ responsabilités distinctes |
| **Comment** | Découper en modules selon cette architecture cible : |

```
code/src/
├── main.py                    # Point d'entrée, boucle principale, scheduling
├── config.py                  # Classe Config, validation .env
├── exchange_client.py         # BinanceFinalClient, ordres, sync timestamp
├── trading_engine.py          # execute_real_trades, buy/sell logic
├── backtest.py                # backtest_from_dataframe, run_all_backtests
├── indicators_calc.py         # calculate_indicators, universal_calculate_indicators
├── display.py                 # Panels Rich, tables, affichage console
├── market_detection.py        # detect_market_changes, display_market_changes
├── state_manager.py           # save_bot_state, load_bot_state, heartbeat
├── cache_manager.py           # safe_cache_read/write, cleanup_expired_cache
├── position_sizing.py         # Les 4 fonctions de sizing
├── email_templates.py         # Templates d'emails (buy, sell, error, cache)
├── email_alert.py             # (existant) envoi SMTP
├── error_handler.py           # (existant) CircuitBreaker
├── exceptions.py              # (existant) Hiérarchie d'exceptions
├── trade_journal.py           # (existant) Journal JSONL
├── walk_forward.py            # (existant) Validation WF
└── watchdog.py                # (existant) Process monitor
```

| | |
|---|---|
| **Méthode** | Extraire module par module en s'appuyant sur les tests de la Phase 3 : `pytest` après chaque extraction pour vérifier zéro régression |
| **Validation** | `wc -l code/src/main.py` < 300 lignes. Chaque module < 500 lignes. `pytest tests/ -v` → 0 échecs |

### 4.2 — Éliminer les variables globales

| | |
|---|---|
| **Effort** | 6h |
| **Quoi** | 17+ variables globales mutables créent des dépendances implicites |
| **Comment** | 1. Créer une classe `TradingBot` qui encapsule `config`, `client`, `bot_state`, `pair_state`, `indicators_cache`. 2. Passer `self` (ou les dépendances nécessaires) en paramètre à chaque fonction. 3. Supprimer les `global pair_state`, `global bot_state`, etc. |
| **Validation** | `grep -c "global " code/src/*.py` → 0 |

### 4.3 — Extraire les templates d'email

| | |
|---|---|
| **Effort** | 2h |
| **Quoi** | 20+ lignes de strings formatées inlinés dans chaque fonction de trading |
| **Comment** | Créer `email_templates.py` avec des fonctions : `buy_success_email(pair, qty, price, ...)`, `sell_success_email(...)`, `error_email(...)`, `cache_cleanup_email(...)`. Chaque fonction retourne `(subject, body)` |
| **Validation** | Aucune construction de body email dans `trading_engine.py`. Chaque email passe par `email_templates.py` |

### 4.4 — Dédupliquer le code d'affichage des résultats

| | |
|---|---|
| **Effort** | 2h |
| **Quoi** | La construction des tables Rich et panels est dupliquée entre `backtest_and_display_results` et `__main__` |
| **Comment** | Créer dans `display.py` : `display_backtest_table(results, pair)`, `display_best_result_panel(best, pair)`, `display_trading_status_panel(...)` |
| **Validation** | Un seul endroit construit chaque type de Table/Panel |

### 4.5 — Rendre `backtest_and_display_results` non dupliquée avec `__main__`

| | |
|---|---|
| **Effort** | 2h |
| **Quoi** | Le `__main__` refait manuellement tout ce que `backtest_and_display_results` fait déjà |
| **Comment** | Simplifier `__main__` pour appeler `backtest_and_display_results()` pour chaque paire au lieu de dupliquer la logique |
| **Validation** | Le `__main__` fait < 80 lignes |

### Checklist Phase 4

- [ ] 4.1 — MULTI_SYMBOLS.py scindé en 12+ modules
- [ ] 4.2 — Zéro variable globale
- [ ] 4.3 — Templates email dans module dédié
- [ ] 4.4 — Affichage dédupliqué
- [ ] 4.5 — `__main__` simplifié
- [ ] **`pytest tests/ -v` → 0 régression. Chaque module < 500 lignes**

---

## PHASE 5 — DURCISSEMENT & DOCUMENTATION

> **Quand** : Semaine 5  
> **Durée** : ~5 heures  
> **Risque si non fait** : Friction pour les futures contributions  
> **Prérequis** : Phase 4 terminée

### 5.1 — Fichier `.env.example`

| | |
|---|---|
| **Effort** | 30 min |
| **Comment** | Créer `.env.example` documenté avec toutes les variables requises et leurs descriptions |

```ini
# === BINANCE API ===
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET_KEY=your_secret_key_here

# === EMAIL ALERTS ===
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your_email@gmail.com
SMTP_PASSWORD=your_app_password
RECEIVER_EMAIL=alerts@example.com
```

### 5.2 — Validation des variables d'environnement au démarrage

| | |
|---|---|
| **Effort** | 1h |
| **Comment** | Ajouter dans `Config.from_env()` une validation stricte : si une variable requise est manquante ou vide, lever `ConfigError` avec un message explicite au lieu de continuer silencieusement |

### 5.3 — Nettoyage des fichiers obsolètes

| | |
|---|---|
| **Effort** | 15 min |
| **Quoi** | Supprimer les fichiers inutiles |
| **Fichiers** | `config/setup_environment.py` (obsolète), `code/src/MULTI_SYMBOLS (2).py` (doublon), `code/src/compare_stoch_methods.py` (utilitaire one-shot) |

### 5.4 — Ajouter `filelock` au requirements.txt

| | |
|---|---|
| **Effort** | 5 min |
| **Quoi** | Le code utilise `filelock` pour le cache mais la dépendance est absente du `requirements.txt` |

### 5.5 — Mettre à jour le README.md

| | |
|---|---|
| **Effort** | 2h |
| **Quoi** | Documenter l'architecture post-refactoring |
| **Contenu** | Architecture des modules, setup initial, commandes de lancement, exécution des tests, stratégie de trading (résumé) |

### 5.6 — Rendre `ecosystem.config.js` portable

| | |
|---|---|
| **Effort** | 30 min |
| **Comment** | Remplacer les chemins absolus par des chemins relatifs ou des variables d'environnement. Utiliser `python` au lieu de `pythonw.exe` pour garder la visibilité stdout |

### 5.7 — Supprimer les adresses email en dur dans les tests

| | |
|---|---|
| **Effort** | 15 min |
| **Réf. audit** | SEC-04 |
| **Comment** | Dans `test_send_mail.py`, lire les adresses depuis `.env` au lieu de les hardcoder |

### Checklist Phase 5

- [ ] 5.1 — `.env.example` créé
- [ ] 5.2 — Validation config au démarrage
- [ ] 5.3 — Fichiers obsolètes supprimés
- [ ] 5.4 — `filelock` dans requirements.txt
- [ ] 5.5 — README.md à jour
- [ ] 5.6 — PM2 config portable
- [ ] 5.7 — Emails en dur supprimés des tests

---

## SUIVI & MÉTRIQUES DE SUCCÈS

### KPIs de progression

| Métrique | Avant (Phase 0) | Cible (Phase 5) |
|---|---|---|
| Score maturité | 4/10 | 8/10 |
| Bugs critiques | 7 | 0 |
| Couverture tests (MULTI_SYMBOLS) | ~2% | ~40% |
| Variables globales | 17+ | 0 |
| Lignes du fichier principal | 5 001 | < 300 (main.py) |
| Modules support intégrés | 0/5 | 5/5 |
| `except Exception` générique | ~40 | < 10 |
| Fichiers de log avec clé API | Oui | Non |

### Critères d'acceptation par phase

| Phase | Critère objectif |
|---|---|
| **0** | `pytest tests/ -v` passe. Bot redémarre sans erreur. Aucune clé API dans les logs |
| **1** | Config modifiable sans toucher au code. Sizing modes testés en backtest |
| **2** | `trade_journal.jsonl` contient des entrées. `heartbeat.json` se met à jour. Logs WF visibles |
| **3** | 40+ tests passent. Couverture backtest + sizing + error_handler + journal |
| **4** | Aucun fichier > 500 lignes. Zéro `global`. `pytest` passe toujours |
| **5** | `.env.example` existe. README à jour. PM2 config portable. Fichiers obsolètes supprimés |

---

## PLANNING RÉCAPITULATIF

```
Semaine 1
├── Jour 1 ─── PHASE 0 : Urgences sécurité & financier      [1h]
├── Jour 2 ─── PHASE 1 : Fiabilité trading (1.1, 1.2, 1.3)  [3h]
├── Jour 3 ─── PHASE 1 : Fiabilité trading (1.4, 1.5)        [3h]
├── Jour 4 ─── PHASE 2 : Modules dormants (2.1, 2.3)         [2h]
├── Jour 5 ─── PHASE 2 : Modules dormants (2.2)              [3h]

Semaine 2
├── Jour 6 ─── PHASE 2 : Modules dormants (2.4)              [3h]
├── Jour 7 ─── PHASE 3 : Tests (3.1, 3.4)                    [3h30]
├── Jour 8 ─── PHASE 3 : Tests (3.2)                         [3h]
├── Jour 9 ─── PHASE 3 : Tests (3.3, 3.5)                    [3h30]
├── Jour 10 ── Validation complète, correction régressions     [2h]

Semaine 3-4
├── PHASE 4 : Refactoring architectural                        [20h]
│   ├── 4.1 Scission modules (8h réparties)
│   ├── 4.2 Élimination globales (6h)
│   ├── 4.3 Templates email (2h)
│   ├── 4.4-4.5 Déduplication (4h)

Semaine 5
└── PHASE 5 : Durcissement & documentation                    [5h]
```

---

*Plan d'action généré à partir de l'audit complet. Chaque phase est indépendamment déployable et vérifiable.*
