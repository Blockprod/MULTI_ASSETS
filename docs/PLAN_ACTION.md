# Plan d'Action — Mise en Production MULTI_ASSETS
_Généré le 4 mars 2026 — basé sur l'audit technique complet_

---

## Vue d'ensemble

| Phase | Contenu | Bloquant prod ? | Effort estimé |
|-------|---------|----------------|---------------|
| **Phase 0** | 5 corrections P0 | ✅ OUI — NO-GO sans elles | ~10h |
| **Phase 1** | 12 corrections P1 | ✅ OUI — avant mainnet | ~14h |
| **Phase 2** | 11 corrections P2 | ⚠️ avant capital plein | ~12h |
| **Phase 3** | 5 améliorations P3 | ❌ dette technique | ~10h |
| **Phase 4** | Déploiement production live | — | continu | ✅ LANCÉ 2026-03-04 |
| **TOTAL** | | | **~46h** |

**Chemin critique** :
```
P0-02 → P0-01 → P0-03 → P0-04 → P0-05
  → P1-04 → P1-10 → Paper 5j → Palier 1 → Capital plein
```

---

## PHASE 0 — Bloquants absolus (NO-GO production)

> Le bot **ne doit pas** toucher au mainnet tant qu'une seule de ces corrections n'est pas appliquée et testée.

### Ordre d'exécution obligatoire : P0-02 → P0-01 → P0-03 → P0-04 → P0-05

---

### P0-01 — Stop-loss non garanti après achat

| Attribut | Valeur |
|----------|--------|
| **Fichiers** | `code/src/exchange_client.py:390` + `code/src/MULTI_SYMBOLS.py:1818` |
| **Dépend de** | P0-02 (doit être résolu avant) |
| **Effort** | 3h |
| **Impact** | Position ouverte sans protection en cas d'échec réseau |

**Problème** :
`place_exchange_stop_loss` est décorée `@log_exceptions(default_return=None)` (`exchange_client.py:390`).
Si les 3 retries échouent, la fonction retourne `None` sans lever d'exception.
Dans `_execute_buy` (`MULTI_SYMBOLS.py:1818`), le retour n'est pas vérifié — la position
reste ouverte sans stop-loss exchange.

**Correction** :
1. Dans `place_exchange_stop_loss` (`exchange_client.py:478`) : retirer `@log_exceptions` et lever `OrderError` si tous les retries échouent
2. Dans `_execute_buy` (`MULTI_SYMBOLS.py:1818`) : entourer le placement SL d'un `try/except OrderError` qui déclenche un `safe_market_sell` de clôture immédiate + alerte email `CRITICAL`
3. Persister `sl_exchange_placed: bool` dans `pair_state` pour détection par la réconciliation au redémarrage

**Test de validation** :
```python
# tests/test_p0_sl_guarantee.py
# 1. Mocker place_exchange_stop_loss pour lever OrderError au 3e retry
# 2. Vérifier que safe_market_sell est appelé immédiatement
# 3. Vérifier que pair_state['in_position'] == False après recovery
# 4. Vérifier qu'un email CRITICAL a été émis
# 5. Vérifier que sl_exchange_placed == False est persisté en état
```

---

### P0-02 — Race condition sur `_live_best_params` et `_last_backtest_time`

| Attribut | Valeur |
|----------|--------|
| **Fichier** | `code/src/MULTI_SYMBOLS.py:776` |
| **Dépend de** | — (à corriger EN PREMIER) |
| **Effort** | 1h |
| **Impact** | Paramètres live corrompus → signaux incohérents |

**Problème** :
`_live_best_params` (ligne 781) et `_last_backtest_time` (ligne 776) sont mutés depuis
les threads du `schedule` sans être sous `_bot_state_lock`, alors que ce lock protège
déjà `bot_state` — incohérence de conception. `_execute_buy` lit `_live_best_params`
pour calculer le SL, ce qui en fait un prérequis direct de P0-01.

**Correction** :
1. Wrapper toutes les lectures/écritures de `_live_best_params` et `_last_backtest_time` avec `with _bot_state_lock:`
2. Alternative : déplacer ces deux dicts dans `bot_state["live_params"]` et `bot_state["backtest_times"]` pour bénéficier du lock existant

**Test de validation** :
```python
# 1. Lancer 4 threads appelant execute_scheduled_trading en parallèle
#    avec threading.Barrier pour forcer les accès simultanés
# 2. Vérifier l'absence de KeyError ou d'état incohérent dans _live_best_params
# 3. Vérifier que _last_backtest_time contient les timestamps corrects pour chaque paire
```

---

### P0-03 — Look-ahead bias : sélection EMA sur dataset complet

| Attribut | Valeur |
|----------|--------|
| **Fichiers** | `code/src/backtest_runner.py:72` + `code/src/indicators_engine.py` |
| **Dépend de** | — |
| **Effort** | 3h |
| **Impact** | Surestimation forte des performances — paramètres live non prédictifs |

**Problème** :
`get_optimal_ema_periods` est appelée avec le DataFrame complet avant la boucle de
simulation dans `backtest_from_dataframe` (`backtest_runner.py:72`). Les paramètres EMA
optimaux pour la période complète sont connus avant la simulation — biais de sélection majeur.
Les résultats backtestés actuels doivent être considérés **invalides**.

**Correction** :
1. Dans `backtest_from_dataframe` : calculer les paramètres EMA sur les 60 premières % du DataFrame uniquement (période IS), figer pour les 40 % OOS
2. Dans `run_walk_forward_validation` (`walk_forward.py:299`) : vérifier que `get_optimal_ema_periods` est appelé sur le fold IS uniquement
3. ⚠️ Re-runner **tous** les backtests après correction — invalider les résultats précédents

**Test de validation** :
```python
# 1. Créer un DataFrame synthétique dont les meilleures EMA sur la 1ère moitié
#    sont (9, 21) et sur la totalité sont (26, 50)
# 2. Vérifier que backtest_from_dataframe utilise (9, 21)
# 3. Vérifier que le Sharpe backtest corrigé est INFÉRIEUR au Sharpe avant correction
#    (si Sharpe supérieur → correction incorrecte)
```

---

### P0-04 — `_current_backtest_pair` global sans lock dans ThreadPoolExecutor

| Attribut | Valeur |
|----------|--------|
| **Fichier** | `code/src/backtest_runner.py:68` |
| **Dépend de** | — |
| **Effort** | 30min |
| **Impact** | Résultats backtest associés à la mauvaise paire → paramètres live corrompus |

**Problème** :
`_current_backtest_pair` (ligne 68) est écrit par `run_single_backtest_optimized`
depuis 4 workers `ThreadPoolExecutor` simultanément (`max_workers=config.max_workers`).
Aucun lock — corruption silencieuse du mapping paire/résultat.

**Correction** :
1. Supprimer la variable globale `_current_backtest_pair`
2. Passer le pair en paramètre explicite dans `args: Tuple` de `run_single_backtest_optimized`
3. Logger le pair depuis le contexte local uniquement

**Test de validation** :
```python
# 1. Lancer run_parallel_backtests avec 4 paires distinctes
# 2. Vérifier que chaque dict résultat contient le bon `pair_symbol`
# 3. Aucun assert failure sur concordance résultat/pair après 10 runs
```

---

### P0-05 — `position_sizing.py` : retour `0.0` silencieux sur exception

| Attribut | Valeur |
|----------|--------|
| **Fichier** | `code/src/position_sizing.py:18` (3 fonctions) |
| **Dépend de** | — |
| **Effort** | 1h |
| **Impact** | Achats silencieusement bloqués sans alerte si entrées corrompues |

**Problème** :
Les 3 fonctions de sizing catchent `Exception` et retournent `0.0`.
Si `atr_value` est `NaN` (données corrompues) ou `entry_price` est `0`,
le sizing échoue silencieusement — le bot tente un achat pour `0.0 USD`,
échoue sur `min_notional`, et ignore la paire sans aucune alerte.

**Correction** :
1. Remplacer `except Exception: return 0.0` par `except Exception as e: logger.error(...); raise SizingError(...)` dans les 3 fonctions
2. Dans `_execute_buy` (`MULTI_SYMBOLS.py:1818`) : catcher `SizingError` explicitement, logger `WARNING`, et skipper la paire sans crash

**Test de validation** :
```python
# 1. Appeler compute_position_size_by_risk(equity=10000, atr_value=float('nan'), entry_price=100)
# 2. Vérifier que SizingError est levé (pas retour silencieux 0.0)
# 3. Vérifier que compute_position_size_fixed_notional et compute_position_size_volatility_parity
#    lèvent aussi SizingError sur entrées invalides
# 4. Vérifier que _execute_buy log WARNING et ne place pas d'ordre si SizingError
```

---

### ✅ Critères de passage Phase 0 → Phase 1

- [ ] `pytest tests/ -k "p0"` — 0 failure
- [ ] Paper trading 24h sur 3 paires — aucune position avec `sl_exchange_placed: False`
- [ ] Tous les backtests re-runnés après P0-03 — nouveaux résultats sauvegardés comme référence
- [ ] `bot_state.json` cohérent après 3 redémarrages forcés

---

## PHASE 1 — Haute priorité (bloquants pour production stable)

> Le bot peut tourner en **paper trading** après la Phase 0. Ces corrections sont requises avant le mainnet avec capital réel.

| ID | Problème | Fichier:Ligne | Dépend de | Effort |
|----|----------|---------------|-----------|--------|
| **P1-01** | `start_date` module-level stale après minuit | `MULTI_SYMBOLS.py:225` | — | 1h |
| **P1-02** | `_last_alert_email_time` non protégé par lock | `error_handler.py:23` | — | 30min |
| **P1-03** | `ErrorHandler.clear_history` mute sans lock | `error_handler.py:220` | — | 30min |
| **P1-04** | Fallback OOS pool complet annule le walk-forward | `MULTI_SYMBOLS.py:2501` | P0-03 | 2h |
| **P1-05** | `global_current_price` écrit sans `global` | `MULTI_SYMBOLS.py:2131` | — | 30min |
| **P1-06** | `save_state` swallows exceptions sans re-raise | `state_manager.py:148` | — | 1h |
| **P1-07** | `sizing_mode='baseline'` default = 95% capital | `MULTI_SYMBOLS.py:2019` | P0-05 | 30min |
| **P1-08** | Synchro timestamp non périodique | `MULTI_SYMBOLS.py:2445` | — | 1h |
| **P1-09** | `conftest.py` ne patche pas `send_trading_alert_email` | `tests/conftest.py:29` | — | 30min |
| **P1-10** | Prix exécution simulé = close signal (pas open+1) | `backtest_runner.py:72` | P0-03 | 3h |
| **P1-11** | `recvWindow=60000` hardcodé à deux endroits | `exchange_client.py:241` + `:515` | — | 30min |
| **P1-12** | `validate_bot_state` informatif seulement, jamais levé | `state_manager.py:73` | P1-06 | 2h |

---

### Détails P1-04 — Fallback OOS annule le walk-forward

**Problème** : Si `_oos_valid_loop` est vide (`MULTI_SYMBOLS.py:2501`), `_pool_loop`
est remplacé par `data['results']` (tous les résultats full-sample) et le meilleur
full-sample est sélectionné pour le live — annulation complète de la protection walk-forward.

**Correction** :
- Si `_oos_valid_loop` est vide → bloquer les nouveaux achats pour cette paire (`pair_state['buy_blocked'] = True`) + alerte email
- Conserver les `_live_best_params` précédents (gel des paramètres)
- Ne jamais dégrader vers le pool full-sample pour la sélection live

**Test de validation** :
```python
# 1. Mocker walk_forward pour retourner 0 résultat OOS valide
# 2. Vérifier que buy_blocked == True pour la paire concernée
# 3. Vérifier qu'aucun ordre d'achat n'est placé pour cette paire
# 4. Vérifier qu'un email d'alerte a été envoyé
```

---

### Détails P1-10 — Prix d'exécution next_open

**Problème** : Acheter au `close` de la bougie signal est physiquement impossible —
le close n'est connu qu'à la fermeture. En live, l'ordre market se remplit à
l'open+slippage de la bougie suivante.

**Correction** :
- Dans `backtest_from_dataframe` (`backtest_runner.py:72`) : sur signal BUY à la bougie `i`, utiliser `df.iloc[i+1]['open']` comme prix d'entrée simulé
- Même logique pour les signaux SELL
- Paramètre `next_open_entry: bool = True` pour rétrocompatibilité
- ⚠️ Re-runner tous les backtests après cette correction

**Test de validation** :
```python
# 1. Créer un DataFrame avec open[i+1] != close[i] sur les bougies signal
# 2. Vérifier que le prix d'entrée dans le résultat = open[i+1]
# 3. Vérifier que le PnL final est différent (inférieur) du PnL avant correction
```

---

### ✅ Critères de passage Phase 1 → Phase 2

- [ ] `pytest tests/ -x` — 0 failure
- [ ] Paper trading 72h sur ≥ 5 paires — `bot_state.json` cohérent après chaque redémarrage
- [ ] Backtest re-runné avec P0-03 + P1-10 — Sharpe OOS ≥ `config.oos_sharpe_min` sur ≥ 3 paires
- [ ] Email d'alerte reçu en test manuel sur erreur simulée
- [ ] Aucun email parasite reçu pendant la suite de tests

---

## PHASE 2 — Priorité moyenne (avant capital plein)

> Le bot peut être en **production palier 1** (capital réduit) pendant cette phase.

| ID | Problème | Fichier:Ligne | Dépend de | Effort | Statut |
|----|----------|---------------|-----------|--------|--------|
| **P2-01** | `partial_enabled` non simulé en backtest | `backtest_runner.py:614` | P1-10 | 3h | ✅ Terminé |
| **P2-02** | `schedule.every(2).minutes` hardcodé | `MULTI_SYMBOLS.py:2406` | — | 1h | ✅ Terminé |
| **P2-03** | `RISK_FREE_RATE = 0.04` hardcodé | `walk_forward.py:34` | — | 30min | ✅ Terminé |
| **P2-04** | Fonctions Windows-only sans garde `os.name` complète | `MULTI_SYMBOLS.py:134` | — | 1h | ✅ Terminé |
| **P2-05** | Logique sélection OOS dupliquée | `MULTI_SYMBOLS.py:882` + `:2501` | P1-04 | 2h | ✅ Terminé |
| **P2-06** | `mock_binance_client` fixture hardcode `BTCUSDC` | `tests/conftest.py:143` | — | 1h | ✅ Terminé |
| **P2-07** | `_EMAIL_COOLDOWN_SECONDS = 300` hardcodé | `error_handler.py:22` | — | 30min | ✅ Terminé |
| **P2-08** | `stoch_rsi < 0.8`, `adx > 25` hardcodés | `backtest_runner.py:569` | — | 1h | ✅ Terminé |
| **P2-09** | `min(len(crypto_pairs), 5)` hardcodé | `backtest_runner.py:820` | — | 30min | ✅ Terminé |
| **P2-10** | Tests manquants : `error_handler`, `trade_journal`, `position_sizing` | `tests/` | P0-05 | 3h | ✅ Terminé |

---

### Détails P2-01 — Simulation des partiels en backtest

**Problème** : `partial_threshold_1/2` et `partial_pct_1/2` sont actifs en live mais
`backtest_from_dataframe` (`backtest_runner.py:614`) ne les simule pas. Le PnL backtest
est calculé sur une sortie complète — divergence structurelle avec le PnL réel.

**Correction** :
- Ajouter dans la boucle de simulation de `backtest_from_dataframe` une logique de partial identique à `_execute_partial_sells` dans MULTI_SYMBOLS
- Paramètre `partial_enabled: bool = config.partial_enabled`
- ⚠️ Re-runner tous les backtests après cette correction

**Test de validation** :
```python
# 1. Lancer backtest_from_dataframe avec partial_enabled=True sur données synthétiques
#    contenant des bougies à +2% et +4%
# 2. Vérifier que le trade_log contient des ventes partielles à ces niveaux
# 3. Vérifier que le PnL total = partial_sell_1 + partial_sell_2 + final_sell
# 4. Comparer avec partial_enabled=False — les deux doivent diverger
```

---

### ✅ Critères de passage Phase 2 → Phase 3

- [ ] `pytest tests/ -v` — couverture ≥ 60% sur `position_sizing.py`, `error_handler.py`, `trade_journal.py`
- [ ] Backtest re-runné avec P2-01 — écart PnL backtest/live ≤ 5% sur les 3 derniers mois paper trading
- [ ] Aucune constante critique (`adx`, `stoch_rsi`, `recvWindow`, `schedule interval`) hors de `Config`

---

## PHASE 3 — Long terme (maintenabilité)

> Améliorations non bloquantes. À planifier en parallèle de l'exploitation.

| ID | Problème | Fichier:Ligne | Dépend de | Effort | Statut |
|----|----------|---------------|-----------|--------|--------|
| **P3-01** | Graceful shutdown via closures fragiles (pas `threading.Event`) | `MULTI_SYMBOLS.py:2717` | — | 2h | ✅ Terminé |
| **P3-02** | `_BACKTEST_THROTTLE_SECONDS = 3600` hors Config | `MULTI_SYMBOLS.py:777` | P2-02 | 30min | ✅ Terminé |
| **P3-03** | `sample_config` fixture manque `backtest_taker_fee` / `backtest_maker_fee` | `tests/conftest.py:58` | P2-10 | 30min | ✅ Terminé |
| **P3-04** | `_execute_real_trades_inner` > 400 lignes — violation SRP | `MULTI_SYMBOLS.py:2045` | P0-01, P1-07 | 4h | ✅ Terminé |
| **P3-05** | Tests d'intégration manquants : buy → partial → sell complet | `tests/` | P2-10, P0-01 | 3h | ✅ Terminé |

---

## PHASE 4 — Déploiement direct en production (sans paper trading)

> Paper trading supprimé — passage direct en production live après validation backtest + 579 tests.

### ~~Étape 4.1 — Paper Trading~~ (SKIP — demande utilisateur)

### Étape 4.2 — Production Live ✅ LANCÉ (2026-03-04 17:00)

**Prérequis** : Phases 0–3 complètes + 579 tests passés.

**Configuration active** :
```ini
PAPER_TRADING=false  # (non défini dans .env — pas de paper trading)
sizing_mode=risk     # (défaut Config)
risk_per_trade=0.05  # (défaut Config)
Paire: SOLUSDT (backtest) / SOLUSDC (live)
Capital: 268.21 USDC
Schedule: toutes les 2 minutes (720 exec/jour)
```

**Statut au lancement** :
- [x] Bot démarré — PID 73464, heartbeat actif, mode RUNNING
- [x] Backtests 60/60 complétés en 3:05
- [x] OOS quality gates actives — achats bloqués (aucun config ne passe Sharpe > 0.3 & WR > 30%)
- [x] Email d'alerte envoyé (OOS gates failed)
- [x] Signal handler SIGTERM + SIGINT enregistrés (P3-01)
- [x] Gestionnaire d'erreurs actif — mode RUNNING

**Critères de sortie (monitoring continu)** :
- [ ] PnL live dans ±15% du PnL backtest corrigé sur la même période
- [ ] Aucun ordre dupliqué détecté (vérification `trade_journal`)
- [ ] Taux de fills SL exchange ≥ 95% (sur les SL déclenchés)
- [ ] 0 incident HTTP 429 / IP ban Binance

---

### Étape 4.3 — Configuration cible

**Prérequis** : Étape 4.2 réussie + Phase 2 complète.

**Configuration** :
```ini
# Capital complet
# Toutes les paires configurées
sizing_mode=risk
partial_enabled=true
```

**Surveillance ongoing** :
- Alerte email si écart PnL backtest/live > 20% sur rolling 30 jours
- Revue mensuelle des paramètres walk-forward (relancer `backtest_and_display_results`)
- Vérification hebdomadaire des `sl_exchange_placed` dans `bot_state.json`

---

## Récapitulatif des dépendances

```
P0-02 ──────────────────────────────────────────► P0-01
P0-03 ──────────────────────────────────────────► P1-04
P0-03 ──────────────────────────────────────────► P1-10
P0-05 ──────────────────────────────────────────► P1-07
P0-05 ──────────────────────────────────────────► P2-10
P1-06 ──────────────────────────────────────────► P1-12
P1-04 ──────────────────────────────────────────► P2-05
P1-10 ──────────────────────────────────────────► P2-01
P2-02 ──────────────────────────────────────────► P3-02
P2-10 ──────────────────────────────────────────► P3-03
P2-10 + P0-01 ──────────────────────────────────► P3-05
P0-01 + P1-07 ──────────────────────────────────► P3-04
```

---

## Tableau de suivi global

| ID | Description courte | Sévérité | Bloquant | Effort | Statut |
|----|-------------------|----------|----------|--------|--------|
| P0-02 | Lock `_live_best_params` | P0 | ✅ | 1h | ✅ Terminé |
| P0-01 | SL garanti post-achat | P0 | ✅ | 3h | ✅ Terminé |
| P0-03 | Look-ahead EMA | P0 | ✅ | 3h | ✅ Terminé |
| P0-04 | `_current_backtest_pair` thread-safe | P0 | ✅ | 30min | ✅ Terminé |
| P0-05 | SizingError au lieu de `0.0` | P0 | ✅ | 1h | ✅ Terminé |
| P1-01 | `start_date` recalculé chaque jour | P1 | ✅ | 1h | ✅ Terminé |
| P1-02 | Lock `_last_alert_email_time` | P1 | ✅ | 30min | ✅ Terminé |
| P1-03 | Lock dans `clear_history` | P1 | ✅ | 30min | ✅ Terminé |
| P1-04 | Bloquer achats si OOS vide | P1 | ✅ | 2h | ✅ Terminé |
| P1-05 | Déclaration `global` manquante | P1 | ✅ | 30min | ✅ Terminé |
| P1-06 | `save_state` re-raise exceptions | P1 | ✅ | 1h | ✅ Terminé |
| P1-07 | Default sizing `risk` pas `baseline` | P1 | ✅ | 30min | ✅ Terminé |
| P1-08 | Resync timestamp périodique | P1 | ✅ | 1h | ✅ Terminé |
| P1-09 | Patch `send_trading_alert_email` en tests | P1 | ✅ | 30min | ✅ Terminé |
| P1-10 | Prix exécution = open+1 | P1 | ✅ | 3h | ✅ Terminé |
| P1-11 | `recvWindow` dans Config | P1 | ✅ | 30min | ✅ Terminé |
| P1-12 | `validate_bot_state` lève exceptions | P1 | ✅ | 2h | ✅ Terminé |
| P2-01 | Simuler partiels en backtest | P2 | ⚠️ | 3h | ✅ Terminé |
| P2-02 | Intervalle schedule dans Config | P2 | ❌ | 1h | ✅ Terminé |
| P2-03 | `RISK_FREE_RATE` dans Config | P2 | ❌ | 30min | ✅ Terminé |
| P2-04 | Garde `os.name` complète | P2 | ❌ | 1h | ✅ Terminé |
| P2-05 | Dédupliquer logique OOS | P2 | ❌ | 2h | ✅ Terminé |
| P2-06 | Fixture multi-paires | P2 | ❌ | 1h | ✅ Terminé |
| P2-07 | `EMAIL_COOLDOWN` dans Config | P2 | ❌ | 30min | ✅ Terminé |
| P2-08 | Seuils `stoch_rsi`/`adx` dans Config | P2 | ❌ | 1h | ✅ Terminé |
| P2-09 | Cap parallel pairs dans Config | P2 | ❌ | 30min | ✅ Terminé |
| P2-10 | Tests `error_handler`, `trade_journal`, `position_sizing` | P2 | ❌ | 3h | ✅ Terminé |
| P3-01 | `threading.Event` pour shutdown | P3 | ❌ | 2h | ✅ Terminé |
| P3-02 | `_BACKTEST_THROTTLE_SECONDS` dans Config | P3 | ❌ | 30min | ✅ Terminé |
| P3-03 | Fixture `backtest_taker_fee` | P3 | ❌ | 30min | ✅ Terminé |
| P3-04 | Découper `_execute_real_trades_inner` | P3 | ❌ | 4h | ✅ Terminé |
| P3-05 | Tests intégration buy→partial→sell | P3 | ❌ | 3h | ✅ Terminé |

---

_Mettre à jour le statut au fil des corrections : ⬜ À faire → 🔄 En cours → ✅ Terminé_
