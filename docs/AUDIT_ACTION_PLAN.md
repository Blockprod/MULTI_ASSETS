# Plan d'Action — Audit Technique MULTI_ASSETS

> Généré le 05/03/2026 — Basé sur l'audit technique complet 7 phases.  
> **Principe** : les corrections sont appliquées étape par étape, sprint par sprint.  
> Aucun fichier de code n'est modifié tant qu'une étape n'est pas validée.

---

## Table des matières

- [Tableau de sévérité](#tableau-de-sévérité)
- [Top 3 risques financiers](#top-3-risques-financiers)
- [Sprint 1 — Critique](#sprint-1--critique)
- [Sprint 2 — Important](#sprint-2--important)
- [Sprint 3 — Amélioration](#sprint-3--amélioration)
- [Points forts conservés](#points-forts-conservés)

---

## Tableau de sévérité

| ID | Sévérité | Composant | Problème | Référence code |
|---|---|---|---|---|
| P5-A | **P0** | `MULTI_SYMBOLS.py` | Aucune limite de perte quotidienne | `_daily_pnl_tracker` jamais utilisé |
| P5-B | **P1** | `MULTI_SYMBOLS.py` | 18 blocs `except Exception: pass` | grep 18 matches |
| P2-A | **P1** | `exchange_client.py` | Client order ID non vérifié avant retry | `safe_market_buy` L275-290 |
| P4-B | **P2** | `bot_config.py` / Cython | Divergence possible constantes Cython vs config | warning C-15 L267-281 |
| P5-C | **P2** | `timestamp_utils.py` | `init_timestamp_solution` retourne toujours True | L162 |
| P3-A | **P2** | `MULTI_SYMBOLS.py` | `_oos_alert_last_sent` non protégé par lock | L901 |
| P6-C | **P2** | `MULTI_SYMBOLS.py` | Liste de scénarios WF dupliquée 4× | L1010, L2740, L2980 |
| P6-A | **P3** | `tests/` | Pas de test corruption état | `test_state_manager.py` |
| P6-B | **P3** | `tests/` | Pas de test intégration backtest→trade | — |

---

## Top 3 risques financiers

### Risque #1 — Absence de daily loss limit (P0)

Le bot peut enchaîner des cycles entry/SL indéfiniment lors d'un flash crash ou d'une haute volatilité.  
Chaque cycle consomme `risk_per_trade × capital` (5.5 % par défaut).  
En 20 cycles (possible en 24h sur bougies 1h), **66 % du capital est détruit**.  
La `CapitalProtectionError` et le `_daily_pnl_tracker` existent dans le code mais ne sont jamais câblés.

### Risque #2 — Ordres dupliqués potentiels sur timeout réseau (P1)

Si le réseau coupe pendant un `safe_market_buy`, le retry renvoie le même `newClientOrderId` mais ne vérifie pas si l'ordre précédent a été partiellement/totalement exécuté.  
Binance rejette normalement les `newClientOrderId` dupliqués, mais certaines conditions de race (latence + timeout) peuvent mener à des résultats inattendus.

---

## Sprint 1 — Critique

> Objectif : éliminer les deux risques pouvant détruire le capital sans avertissement.

### Étape 1.1 — Implémenter la limite de perte quotidienne (P5-A)

**Fichiers concernés** : `MULTI_SYMBOLS.py`, `bot_config.py`

**Description** :
- Ajouter `daily_loss_limit_pct: float = 0.05` dans la classe `Config` (`bot_config.py`).
- Initialiser `_daily_pnl_tracker` dans `bot_state` au démarrage avec la date du jour et le capital initial.
- Dans `execute_real_trades`, après chaque vente (stop-loss ou signal), calculer le PnL cumulatif de la journée.
- Si `pnl_journalier / capital_initial <= -config.daily_loss_limit_pct` → lever `CapitalProtectionError`, bloquer tous les nouveaux achats, envoyer un email CRITIQUE.
- Réinitialiser le tracker à minuit UTC.

**Comportement attendu** :
- Les ventes (stops, partiels) restent actives même si la limite est atteinte.
- Seuls les nouveaux achats sont bloqués.
- Le blocage est persisté dans `bot_state` pour survivre à un redémarrage.

---

### Étape 1.2 — Remplacer les 18 blocs `except Exception: pass` (P5-B)

**Fichier concerné** : `MULTI_SYMBOLS.py`, `email_utils.py`, `display_ui.py`, `cache_manager.py`, `watchdog.py`

**Description** :
Remplacer chaque occurrence de :
```python
except Exception:
    pass
```
par :
```python
except Exception as _e:
    logger.warning("...[contexte]: %s", _e)
```

**Occurrences prioritaires dans `MULTI_SYMBOLS.py`** :
| Ligne | Contexte | Action |
|---|---|---|
| L429 | Échec annulation SL exchange — email alert | `logger.warning("[SL-CANCEL] Email alerte impossible: %s", _e)` |
| L521 | `load_bot_state` error notification | `logger.warning("[STATE] notification erreur impossible: %s", _e)` |
| L544 | `load_bot_state` RuntimeError notification | `logger.warning("[STATE] notification erreur impossible: %s", _e)` |
| L742 | Backtest throttle check | `logger.warning("[SCHEDULED] Erreur panel trading: %s", _e)` |
| L1176 | Scheduled error alert email | `logger.warning("[SCHEDULED] Email alerte globale impossible: %s", _e)` |
| L1234 | Live-only error alert email | `logger.warning("[LIVE-ONLY] Email alerte impossible: %s", _e)` |
| L3321 | Startup error alert email | `logger.warning("[SHUTDOWN] Email alerte impossible: %s", _e)` |

---

## Sprint 2 — Important

> Objectif : éliminer les risques d'ordres fantômes.

### Étape 2.1 — Vérification d'ordre existant avant retry (P2-A)

**Fichier concerné** : `exchange_client.py`

**Description** :
Dans `safe_market_buy` et `safe_market_sell`, avant chaque tentative de retry (à partir de la tentative 2), interroger l'API Binance pour vérifier si un ordre avec le même `client_id` est déjà FILLED ou en cours :

```python
# Pseudo-code de la correction
for attempt in range(max_retries):
    if attempt > 0:
        try:
            existing = client.get_order(symbol=symbol, origClientOrderId=client_id)
            if existing.get('status') in ('FILLED', 'PARTIALLY_FILLED'):
                logger.info(f"Order already {existing['status']}, skipping retry")
                return existing
        except Exception:
            pass  # ordre non trouvé → retry normal
    # ... logique d'envoi d'ordre existante
```

**Impact** : Empêche l'accumulation d'ordres dupliqués lors de timeouts réseau.

---

## Sprint 3 — Amélioration

> Objectif : réduire la dette technique et améliorer la testabilité.

### Étape 3.1 — Extraire `WF_SCENARIOS` en constante unique (P6-C)

**Fichier concerné** : `MULTI_SYMBOLS.py`

**Description** :
Remplacer les 4 définitions inline identiques de la liste de scénarios WF par une constante module-level :

```python
# À placer après SCENARIO_DEFAULT_PARAMS (L299)
WF_SCENARIOS: list = [
    {'name': 'StochRSI',     'params': {'stoch_period': 14}},
    {'name': 'StochRSI_SMA', 'params': {'stoch_period': 14, 'sma_long': 200}},
    {'name': 'StochRSI_ADX', 'params': {'stoch_period': 14, 'adx_period': 14}},
    {'name': 'StochRSI_TRIX','params': {'stoch_period': 14, 'trix_length': 7, 'trix_signal': 15}},
]
```

Références à remplacer : L1010, L2740, L2980.

---

### Étape 3.2 — Protéger `_oos_alert_last_sent` par un lock (P3-A)

**Fichier concerné** : `MULTI_SYMBOLS.py`

**Description** :
Ajouter un `_oos_alert_lock = threading.Lock()` à côté de `_oos_alert_last_sent` (L901) et entourer les lectures/écritures de ce dict dans `apply_oos_quality_gate`.

---

### Étape 3.3 — Tests de corruption d'état (P6-A)

**Fichier concerné** : `tests/test_state_manager.py`

**Cas à couvrir** :
- Fichier HMAC corrompu → `load_state()` retourne `{}` sans exception.
- Fichier tronqué à mi-écriture → même comportement.
- Clé `JSON_V1:` présente mais signature incorrecte → comportement identique.
- Migration fichier pickle legacy V1 → format JSON correct.

---

### Étape 3.4 — Vérifier que le `.pyd` Cython utilise les paramètres runtime (P4-B)

**Fichiers concernés** : `code/backtest_engine_standard.pyx`, `code/backtest_runner.py`

**Description** :
Inspecter le `.pyx` compilé pour confirmer que `atr_multiplier` et `atr_stop_multiplier` passés en paramètre à `backtest_from_dataframe_fast` écrasent bien les `DEF` constantes à la compilation.  
Si ce n'est pas le cas, modifier le `.pyx` pour accepter ces valeurs comme paramètres `double` et recompiler le `.pyd`.

---

### Étape 3.5 — Corriger `init_timestamp_solution` (P5-C)

**Fichier concerné** : `timestamp_utils.py`

**Description** :
À la ligne L162, modifier le return pour refléter le succès ou l'échec réel de la synchronisation :

```python
# Avant
return True  # toujours True même sur échec

# Après
return sync_success  # bool retourné par _perform_ultra_robust_sync
```

---

## Points forts conservés

Ces mécanismes sont bien implémentés et **ne doivent pas être modifiés** :

| Force | Implémentation | Fichier |
|---|---|---|
| Protection SL exchange | P0-01 : 3 retry → rollback → emergency halt | `MULTI_SYMBOLS.py` L2197-L2280 |
| Intégrité état | JSON + HMAC-SHA256 + écriture atomique `.tmp → os.replace` | `state_manager.py` L155 |
| Walk-Forward OOS | Anchored expanding-window + decay gate anti-overfit | `walk_forward.py` |
| Réconciliation startup | C-03 + C-11 : orphelins détectés, SL reposés | `MULTI_SYMBOLS.py` L570-L710 |
| Rate limiter | Token bucket thread-safe 18 req/s | `exchange_client.py` L28-L55 |
| Circuit breaker | 3 failures → pause 5 min → auto-recovery | `error_handler.py` L66 |
| Cohérence signal buy/sell | F-COH : params verrouillés depuis l'entrée | `MULTI_SYMBOLS.py` L2548 |
| Graceful shutdown | SIGTERM/SIGINT/atexit + vérification stops | `MULTI_SYMBOLS.py` L3150-L3400 |

---

*Fin du plan d'action — prêt pour exécution étape par étape.*
