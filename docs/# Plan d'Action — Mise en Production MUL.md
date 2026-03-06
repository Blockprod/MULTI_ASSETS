# Plan d'Action — Mise en Production MULTI_ASSETS (Binance Spot)

> **Généré le** : 03 mars 2026  
> **Basé sur** : Audit technique complet du workspace MULTI_ASSETS  
> **Statut** : Approuvé — corrections à appliquer étape par étape

---

## Résumé exécutif

| Métrique | Valeur |
|----------|--------|
| Corrections bloquantes (P0) | 3 |
| Corrections haute priorité (P1) | 5 |
| Corrections moyenne priorité (P2) | 5 |
| Améliorations long terme (P3) | 4 |
| Effort total estimé | ~55h |
| Durée minimale avant production | 14j (paper) + 30j (palier 1) |

**Points forts à conserver (ne pas modifier) :**
- Graceful shutdown — déjà implémenté (`MULTI_SYMBOLS.py:2354-2356`)
- HMAC-SHA256 sur le pickle d'état — déjà implémenté (`state_manager.py`)
- Rate limiter + retry — déjà implémentés et fonctionnels (`exchange_client.py`)
- Per-pair non-blocking locks — `MULTI_SYMBOLS.py:936+`
- `_bot_state_lock` (RLock) — `MULTI_SYMBOLS.py:245`
- Réconciliation API au démarrage — `MULTI_SYMBOLS.py:384`
- Walk-forward intégré — `walk_forward.py`
- `clientOrderId` idempotent — `exchange_client.py`
- `filelock` pour le cache — `cache_manager.py`

---

## PHASE 0 — Corrections bloquantes (P0)

> ⛔ **Sans ces 3 corrections, le bot NE DOIT PAS démarrer, même en paper trading.**

| ID | Correction | Fichier:Ligne | Effort | Dépendances |
|----|-----------|---------------|--------|-------------|
| C-01 | Paper trading guard dans toutes les méthodes d'ordre | `exchange_client.py` | 3h | Aucune |
| C-02 | Remplacer `STOP_LOSS_LIMIT` par `STOP_LOSS` (market trigger) | `exchange_client.py:478` | 2h | Aucune |
| C-03 | Bloquer le trading (flat) si 0 résultat OOS-valide — supprimer le fallback full-sample | `MULTI_SYMBOLS.py:2194` | 2h | Aucune |

---

### C-01 — Paper Trading Guard

**Problème**  
Aucun guard dans `exchange_client.py`. Un appel à `safe_market_buy`, 
`safe_market_sell`, `place_stop_loss_order` ou `place_exchange_stop_loss` 
est exécuté réellement même si `config.paper_trading = True`. Un appel réel 
à Binance est possible accidentellement en mode paper.

**Fichier cible** : `code/src/exchange_client.py`  
**Méthodes à protéger** :
- `place_stop_loss_order` (ligne ~385)
- `place_exchange_stop_loss` (ligne ~478)
- `safe_market_buy`
- `safe_market_sell`
- `cancel_order`

**Pattern de correction**  
Ajouter en tête de chaque méthode d'ordre le guard suivant :

```python
if getattr(config, 'paper_trading', False):
    logger.info("[PAPER] %s simulé — aucun appel Binance envoyé", '<nom_méthode>')
    return {
        'status': 'FILLED',
        'paper': True,
        'orderId': f'PAPER_{uuid4().hex[:8]}',
        'executedQty': str(quantity),
        'cummulativeQuoteQty': str(round(quantity * price, 8)),
    }
```

Le retour simulé doit avoir **exactement** le format attendu par 
`_execute_real_trades_inner` pour chaque type d'ordre.

**Test de validation C-01**
1. Démarrer avec `PAPER_TRADING=true` dans `.env`
2. Vérifier qu'aucun appel réseau vers `api.binance.com` n'est émis 
   (intercepter avec un mock de `requests.Session.send`)
3. Vérifier que les retours simulés ont le format attendu 
   (champs `status`, `orderId`, `executedQty` présents)
4. Vérifier dans les logs la présence de `[PAPER]` pour chaque ordre simulé
5. ✅ **Gate** : `grep -c "PAPER" bot.log > 0` ET 
   `grep -c "api.binance.com" network.log == 0`

---

### C-02 — Ordre Stop Garanti (STOP_LOSS vs STOP_LOSS_LIMIT)

**Problème**  
`place_exchange_stop_loss` (ligne ~478) utilise `STOP_LOSS_LIMIT`. 
Si le prix gap en dessous du `limitPrice` lors d'un flash crash ou d'un 
mouvement rapide, l'ordre n'est pas rempli et la position reste exposée 
sans aucune protection. La fonction `place_stop_loss_order` (ligne ~385) 
utilise déjà `STOP_LOSS` correctement.

**Fichier cible** : `code/src/exchange_client.py:478`

**Pattern de correction**  
Remplacer l'appel avec `type='STOP_LOSS_LIMIT'` et `price=limitPrice` 
par `type='STOP_LOSS'` sans `price`. Utiliser `place_stop_loss_order` 
(ligne ~385) comme implémentation de référence pour unifier les deux fonctions.

> ⚠️ **Ne pas** contourner en abaissant le `limitPrice` — cela masque 
> le problème sans le résoudre sur les gaps extrêmes.

**Test de validation C-02**
1. En paper trading (après C-01) : vérifier que le retour simulé 
   a `type == 'STOP_LOSS'`
2. Sur **Binance Testnet** : placer un stop réel et vérifier 
   l'acceptance du type d'ordre
3. Simuler un refus d'ordre (min_notional non atteint) : vérifier 
   qu'une alerte email est envoyée ET que la position est loguée 
   comme `"stop_loss_placed": False`
4. ✅ **Gate** : `grep -c "STOP_LOSS_LIMIT" exchange_client.py == 0`

---

### C-03 — Suppression du fallback full-sample OOS

**Problème**  
`MULTI_SYMBOLS.py:2194` — si `_oos_valid_loop` est vide 
(aucun résultat ne passe les quality gates OOS), le code fait :  
`_pool_loop = data['results']`  
Le bot trade alors avec des paramètres full-sample potentiellement 
surajustés, annulant l'utilité du walk-forward. Le mécanisme `oos_blocked` 
existe déjà dans `bot_state` mais n'est pas activé ici.

**Fichier cible** : `code/src/MULTI_SYMBOLS.py:2194`

**Pattern de correction**
```python
# Remplacer le bloc existant (lignes ~2193-2196) par :
if not _oos_valid_loop:
    logger.critical(
        "[OOS-BLOCK] %s : 0 résultat OOS-valide — paire mise en flat (C-03). "
        "Vérifier les quality gates : sharpe_min=%.2f, win_rate_min=%.2f",
        backtest_pair, config.oos_sharpe_min, config.oos_win_rate_min
    )
    with _bot_state_lock:
        bot_state.setdefault(backtest_pair, {})['oos_blocked'] = True
        bot_state[backtest_pair]['oos_blocked_since'] = datetime.utcnow().isoformat()
    save_bot_state(force=True)
    continue  # skip to next pair — aucun ordre ne sera posté
```

Vérifier que le guard `oos_blocked` dans `_execute_real_trades_inner` 
est bien actif et court-circuite l'exécution.

**Test de validation C-03**
1. Unit test : mock `validate_oos_result` pour retourner `False` 
   sur tous les résultats
2. Vérifier que `bot_state[pair]['oos_blocked'] == True`
3. Vérifier qu'aucun ordre n'est posté pour la paire bloquée 
   (log `OOS-BLOCK` present, 0 appel `safe_market_buy`)
4. Vérifier que les autres paires (non bloquées) continuent normalement
5. ✅ **Gate** : `grep -c "OOS-BLOCK" bot.log > 0` ET 
   `grep -c "safe_market_buy.*PAIR_BLOQUÉE" bot.log == 0`

---

### Gate de sortie Phase 0

> ✅ **Les 3 conditions suivantes doivent être vérifiées simultanément 
> avant de passer à la Phase 1 :**

- [ ] 0 appel Binance réel en mode `PAPER_TRADING=true` (vérifiable via mock réseau)
- [ ] Ordre stop de type `STOP_LOSS` confirmé sur Binance Testnet (ou retour simulé correct)
- [ ] Paire OOS-bloquée ne génère aucun ordre (log `[OOS-BLOCK]` présent)

---

## PHASE 1 — Corrections Haute Priorité (P1)

> ⚠️ **À corriger dans les 48h suivant la validation de Phase 0. 
> Bloquantes pour le passage en production réelle.**

| ID | Correction | Fichier:Ligne | Effort | Dépendances |
|----|-----------|---------------|--------|-------------|
| C-04 | Escalader `load_bot_state` failure en CRITICAL + alerte email | `MULTI_SYMBOLS.py:355` | 2h | Aucune |
| C-05 | Ne purger `oos_blocked` qu'après un backtest validé, pas au chargement | `MULTI_SYMBOLS.py:369-373` | 1h | C-03 |
| C-06 | Tests : `exchange_client.py` — ordres, stop-loss, paper guard | `tests/` | 8h | C-01, C-02 |
| C-07 | Tests : `position_sizing.py` — 4 modes + edge cases | `tests/` | 6h | Aucune |
| C-08 | Tests : `state_manager.py` — load/save/corruption/HMAC | `tests/` | 4h | C-04 |

---

### C-04 — Escalade `load_bot_state` failure

**Problème**  
Le décorateur `@log_exceptions(default_return=None)` (ligne 355) retourne 
`None` silencieusement si `load_state()` échoue (fichier corrompu, erreur I/O, 
HMAC invalide). `bot_state` reste `{}` → toutes les positions ouvertes sont 
oubliées → le bot pourrait racheter les mêmes actifs déjà détenus.

La réconciliation API (ligne 2155) atténue partiellement ce risque 
mais ne couvre pas le cas où la réconciliation échoue également.

**Fichier cible** : `code/src/MULTI_SYMBOLS.py:355`

**Pattern de correction**
```python
def load_bot_state() -> None:
    """
    Charge l'état persisté depuis le disque.
    En cas d'échec (corruption, HMAC invalide, I/O error) :
      - Log CRITICAL avec stacktrace
      - Alerte email envoyée
      - Démarrage avec état vide (la réconciliation API prend le relais)
    """
    global bot_state
    loaded = load_state()  # Retourne None si échec ou HMAC invalide
    if loaded is None:
        logger.critical(
            "[STATE-CRITICAL] Impossible de charger bot_state — "
            "démarrage avec état vide. Réconciliation API obligatoire."
        )
        _error_notification_handler(
            "load_bot_state a retourné None (fichier corrompu ou absent). "
            "Réconciliation automatique en cours.",
            "STATE_LOAD_FAILED"
        )
        return  # continue avec bot_state = {} — reconcile prend le relais
    with _bot_state_lock:
        bot_state.update(loaded)
        logger.info("[STATE] bot_state chargé — %d paires", len(bot_state))
```

**Test de validation C-04**
1. Corrompre `bot_state.pkl` : `python -c "open('bot_state.pkl','wb').write(b'CORRUPT')"`
2. Démarrer le bot → vérifier log `[STATE-CRITICAL]` + email d'alerte reçu
3. Vérifier que la réconciliation API s'exécute et reconstruit l'état partiellement
4. Vérifier que le bot ne crashe pas et continue (mode dégradé acceptable)
5. ✅ **Gate** : `grep -c "STATE-CRITICAL" bot.log > 0` ET email reçu

---

### C-05 — Purge `oos_blocked` conditionnelle au rechargement

**Problème**  
`MULTI_SYMBOLS.py:369-373` purge systématiquement `oos_blocked` et 
`oos_blocked_since` à chaque chargement d'état. Résultat : un simple 
redémarrage du bot **efface les flags OOS** posés par C-03, réactivant 
des paires qui ne devraient pas trader.

**Fichier cible** : `code/src/MULTI_SYMBOLS.py:369-373`

**Principe de correction**  
Supprimer la purge automatique au chargement.  
Déplacer la logique de purge de `oos_blocked` dans `run_parallel_backtests()`, 
uniquement **après** qu'un nouveau résultat OOS-valide soit disponible 
pour la paire concernée.

**Dépendance** : Doit être fait **après C-03** — le mécanisme de blocage 
doit exister avant d'en protéger la persistance.

**Test de validation C-05**
1. Poser `oos_blocked = True` pour une paire dans `bot_state`
2. Sauvegarder l'état : `save_bot_state(force=True)`
3. Redémarrer le bot
4. Vérifier que `bot_state[pair]['oos_blocked'] == True` après chargement
5. Simuler un backtest validé OOS → vérifier que le flag est purgé
6. ✅ **Gate** : flag `oos_blocked=True` survivant à un redémarrage complet

---

### C-06 — Tests `exchange_client.py`

**Problème**  
0% de couverture sur le module le plus critique côté ordres réels. 
Aucun test couvre : `safe_market_buy`, `safe_market_sell`, 
`place_stop_loss_order`, `place_exchange_stop_loss`, le paper guard (C-01), 
le type d'ordre stop (C-02).

**Fichier cible** : `tests/test_exchange_client.py` (à créer)

**Cas à couvrir**
```
- safe_market_buy : succès, solde insuffisant, API timeout
- safe_market_sell : succès, quantité insuffisante, min_notional rejeté
- place_stop_loss_order : succès, échec → alerte email
- place_exchange_stop_loss : type STOP_LOSS confirmé (pas STOP_LOSS_LIMIT)
- Paper guard : PAPER_TRADING=true → 0 appel réseau réel pour chaque méthode
- cancel_order : succès, ordre déjà annulé (idempotent)
- Rate limiter : vérifier qu'il n'est pas bypassé sur N appels rapides
```

**Contrainte** : Tous les tests doivent utiliser des mocks de 
`requests.Session` ou `binance.client.Client`. 
**Aucune connexion réelle à Binance.**

**Test de validation C-06**
- `pytest tests/test_exchange_client.py -v --cov=code/src/exchange_client --cov-report=term`
- ✅ **Gate** : couverture ≥ 70% sur `exchange_client.py`, 0 test skipped

---

### C-07 — Tests `position_sizing.py`

**Problème**  
0% de couverture sur les 4 modes de sizing (baseline, risk, 
fixed_notional, vol_parity). Un bug de sizing peut surexposer 
le capital à chaque trade.

**Fichier cible** : `tests/test_position_sizing.py` (à créer)

**Cas à couvrir**
```
- Mode baseline : calcul standard, solde insuffisant
- Mode risk : ATR manquant (→ fallback), volatilité extrême
- Mode fixed_notional : arrondi LOT_STEP correct
- Mode vol_parity : division par zéro sur vol nulle
- Toutes combinaisons : résultat ≥ min_notional ou retour 0
- Résultat jamais négatif, jamais supérieur au solde disponible
```

**Test de validation C-07**
- `pytest tests/test_position_sizing.py -v --cov=code/src/position_sizing --cov-report=term`
- ✅ **Gate** : couverture ≥ 80% sur `position_sizing.py`

---

### C-08 — Tests `state_manager.py`

**Problème**  
0% de couverture sur le module de persistance. Un bug ici efface 
l'état global du bot. La validation HMAC (protection contre corruption) 
n'est jamais testée.

**Fichier cible** : `tests/test_state_manager.py` (à créer)

**Cas à couvrir**
```
- save_state + load_state : round-trip complet
- Fichier absent : load_state retourne None (pas d'exception)
- Fichier corrompu (bytes aléatoires) : retourne None + log WARNING
- HMAC invalide (fichier tampered) : retourne None + log WARNING
- Écriture atomique : vérifier qu'un fichier .tmp est créé puis renommé
- Backup : vérifier la présence du backup avant réécriture
```

**Dépendance** : Doit être fait **après C-04** pour tester le comportement 
de `load_bot_state` en mode dégradé.

**Test de validation C-08**
- `pytest tests/test_state_manager.py -v --cov=code/src/state_manager --cov-report=term`
- ✅ **Gate** : couverture ≥ 85% sur `state_manager.py`

---

### Gate de sortie Phase 1

> ✅ **Les 5 conditions suivantes doivent être vérifiées :**

- [ ] Alerte email reçue sur corruption pickle (C-04 validé)
- [ ] Redémarrage conserve `oos_blocked` (C-05 validé)
- [ ] `pytest tests/test_exchange_client.py` passe, couverture ≥ 70% (C-06)
- [ ] `pytest tests/test_position_sizing.py` passe, couverture ≥ 80% (C-07)
- [ ] `pytest tests/test_state_manager.py` passe, couverture ≥ 85% (C-08)
- [ ] `pytest -x` global sans erreur (régression 0)

---

## PHASE 2 — Corrections Moyenne Priorité (P2)

> ℹ️ **Non bloquantes pour le palier 1 de production, 
> mais obligatoires avant la configuration cible (toutes paires, 100% capital).**

| ID | Correction | Fichier:Ligne | Effort | Dépendances |
|----|-----------|---------------|--------|-------------|
| C-09 | Remplacer `3` hardcodé par `config.atr_stop_multiplier` | `MULTI_SYMBOLS.py:1342` | 30min | Aucune |
| C-10 | Backtest : exécution à `next_open` au lieu du `close` signal | Backtest engine | 4h | Tests P1 en place |
| C-11 | Tests : `error_handler.py` — CircuitBreaker states, reset, alert | `tests/` | 3h | Aucune |
| C-12 | Tests : `_execute_real_trades_inner` — buy/sell/partial/trailing (mocks) | `tests/` | 12h | C-01 (paper guard) |
| C-13 | Extraire `_get_coin_balance()` helper — dédupliquer `free + locked` | `MULTI_SYMBOLS.py:1003/404` | 1h | Aucune |

---

### C-09 — Stop multiplier — source de vérité unique

**Problème**  
`MULTI_SYMBOLS.py:1342` utilise le littéral `3` pour calculer 
`stop_loss_at_entry`, alors que `config.atr_stop_multiplier = 3.0` 
est la source de vérité (utilisée ligne 987). Si la config est modifiée 
(`ATR_STOP_MULTIPLIER=2.5`), le `setdefault` de `stop_loss_at_entry` 
utilisera encore `3`, rendant le live incohérent avec le backtest.

**Fichier cible** : `code/src/MULTI_SYMBOLS.py:1342`

**Correction** : 1 ligne.  
Remplacer `3 * (row.get('atr') or 0.0)` 
par `atr_stop_multiplier * (row.get('atr') or 0.0)`.  
La variable `atr_stop_multiplier` est déjà dans le scope local (ligne 987).

**Test de validation C-09**
1. Modifier `.env` : `ATR_STOP_MULTIPLIER=2.5`
2. Déclencher un achat en paper trading
3. Vérifier dans les logs : `stop_loss_at_entry = entry_price - 2.5 * atr`
4. ✅ **Gate** : `grep -c "3 \* " MULTI_SYMBOLS.py` trouvant la ligne 1342 == 0

---

### C-10 — Prix d'exécution backtest : next_open

**Problème**  
Le signal est généré à la clôture de la bougie `t` mais le backtest 
s'exécute au `close[t]`. En réalité, l'ordre market est passé **après** 
la clôture de `t` et exécuté à l'ouverture de `t+1` ou au meilleur prix marché.

**Impact estimé** : Réduction de 0.1–0.5% des performances backtestées.  
C'est une correction de **réalisme statistique**, pas un bug critique.

**Fichier cible** : Backtest engine (boucle de simulation dans `MULTI_SYMBOLS.py`)

**Pattern de correction**  
Dans la boucle de simulation, remplacer le prix d'entrée/sortie :
```python
# Avant
execution_price = row['close']

# Après
# Utiliser l'open de la bougie suivante si disponible
next_idx = i + 1
execution_price = (
    df.iloc[next_idx]['open']
    if next_idx < len(df)
    else row['close']  # Dernière bougie : fallback au close
)
```

**Dépendance** : Doit être fait **après les tests P1** — 
le changement de prix d'exécution modifie les résultats de backtest 
et nécessite un filet de tests pour valider la non-régression.

**Test de validation C-10**
1. Exécuter un backtest sur BTC/USDT 1h avant et après la correction
2. Vérifier que le PnL total est légèrement inférieur (plus réaliste)
3. Vérifier que le nombre de trades est identique (seul le prix change)
4. Vérifier l'absence d'`IndexError` sur la dernière bougie
5. ✅ **Gate** : backtest complet sans erreur, PnL ≤ PnL avant correction

---

### C-11 — Tests `error_handler.py` (CircuitBreaker)

**Cas à couvrir**
```
- CircuitBreaker : transition CLOSED → OPEN après N échecs
- CircuitBreaker : transition OPEN → HALF_OPEN après timeout
- CircuitBreaker : reset sur succès en HALF_OPEN
- ErrorHandler : callback appelé sur erreur critique
- ErrorHandler : pas de double-alerte sur erreurs répétées (dedup)
```

**Test de validation C-11**
- `pytest tests/test_error_handler.py -v --cov=code/src/error_handler --cov-report=term`
- ✅ **Gate** : couverture ≥ 75% sur `error_handler.py`

---

### C-12 — Tests `_execute_real_trades_inner`

**Problème**  
La fonction la plus critique du bot (~700 lignes) a 0% de couverture. 
Aucun test couvre les chemins buy, sell, partial vente, trailing stop, 
stop-loss, skip via `oos_blocked`.

**Fichier cible** : `tests/test_trading_engine.py` (à créer)

**Cas à couvrir**
```
- Achat réussi : vérifier mise à jour bot_state (entry_price, qty, stop placé)
- Achat échoué (API error) : vérifier état non modifié
- Vente complète : vérifier bot_state nettoyé
- Vente partielle 1 : vérifier partial_taken_1=True, qty réduite
- Vente partielle 2 : vérifier partial_taken_2=True
- Double-exécution partielle : per-pair lock empêche la répétition
- Trailing stop update : vérifier progression du stop
- oos_blocked=True : vérifier 0 ordre passé
- Solde insuffisant pour min_notional : vérifier skip gracieux
```

**Dépendance** : Doit être fait **après C-01** (paper guard) — 
les mocks utilisent le même pattern de retour simulé.

**Test de validation C-12**
- `pytest tests/test_trading_engine.py -v --cov=code/src/MULTI_SYMBOLS --cov-report=term`
- ✅ **Gate** : couverture ≥ 60% sur `MULTI_SYMBOLS.py`, 
  tous les chemins `buy/sell/partial` couverts

---

### C-13 — Helper `_get_coin_balance()`

**Problème**  
Le pattern `next((b for b in account_info['balances'] if b['asset'] == X), None)` 
suivi de `float(b['free']) + float(b['locked'])` est répété à au moins 
2 endroits (`MULTI_SYMBOLS.py:1003` et `:404`). Si l'un change la logique 
(ex : exclure `locked`), l'autre reste incohérent.

**Correction** : Extraire une fonction helper privée dans `MULTI_SYMBOLS.py` :
```python
def _get_coin_balance(account_info: dict, asset: str) -> float:
    """Retourne free + locked pour un asset depuis account_info."""
    bal = next(
        (b for b in account_info.get('balances', []) if b['asset'] == asset),
        None
    )
    if bal is None:
        return 0.0
    return float(bal.get('free', 0)) + float(bal.get('locked', 0))
```

**Test de validation C-13**
- Vérifier que les 2 occurrences utilisent `_get_coin_balance()`
- Unit test : balance présente, balance absente, balance à 0
- ✅ **Gate** : `grep -c "b\['free'\].*b\['locked'\]" MULTI_SYMBOLS.py == 0`

---

### Gate de sortie Phase 2

> ✅ **Les conditions suivantes doivent être vérifiées :**

- [ ] `ATR_STOP_MULTIPLIER=2.5` → stop calculé à `2.5 * atr` (C-09)
- [ ] Backtest next_open sans erreur, PnL ≤ PnL avant (C-10)
- [ ] `pytest tests/test_error_handler.py` couverture ≥ 75% (C-11)
- [ ] `pytest tests/test_trading_engine.py` couverture ≥ 60% (C-12)
- [ ] `pytest -x` global sans erreur (régression 0)

---

## PHASE 3 — Améliorations Long Terme (P3)

> 📈 **Maintenabilité et robustesse statistique. 
> Non bloquantes pour la production.**

| ID | Amélioration | Fichier:Ligne | Effort | Dépendances |
|----|-------------|---------------|--------|-------------|
| C-14 | Expanding window pour indicateurs backtest (élimination biais look-ahead) | Backtest engine | 1 sem | C-10, tests P1 |
| C-15 | Refactoring `_execute_real_trades_inner` (~700 lignes → sous-fonctions) | `MULTI_SYMBOLS.py:984` | 1 sem | C-12 (tests obligatoires avant) |
| C-16 | Remplacer `Dict[str, Any]` par `TypedDict` pour `bot_state` | `MULTI_SYMBOLS.py:241` | 3j | C-15 |
| C-17 | Migration pickle → JSON avec validation de schéma | `state_manager.py` | 2j | C-16 (schéma TypedDict) |

---

### C-14 — Expanding window pour les indicateurs

**Problème**  
Les indicateurs (EMA, RSI, StochRSI, ATR) sont calculés sur le dataset 
complet avant la boucle de simulation. Les premières valeurs bénéficient 
d'un contexte futur qu'elles ne devraient pas avoir (biais look-ahead résiduel).

**Effort** : 1 semaine — implique de recalculer les indicateurs 
bougie par bougie en expanding window, ce qui augmente la complexité 
algorithmique O(n) → O(n²) sans optimisation.

**Implémentation recommandée** : Utiliser les fonctions incrémentales 
de `pandas_ta` ou implémenter un calcul EMA incrémental (plus rapide 
qu'un recalcul complet à chaque bougie).

---

### C-15 — Refactoring `_execute_real_trades_inner`

**Note critique** : Ce refactoring 
**NE DOIT PAS précéder les tests C-12**. 
Refactorer ~700 lignes sans couverture de tests = risque certain 
de régression en production. L'ordre est impératif :  
`C-12 (tests) → C-15 (refactoring)`.

**Découpage suggéré** :
```
_execute_real_trades_inner()
  ├── _check_entry_conditions()      # Vérif signaux + guards
  ├── _execute_buy()                 # Achat + stop placement
  ├── _execute_partial_sell()        # Ventes partielles 1 & 2
  ├── _execute_full_sell()           # Vente complète
  ├── _update_trailing_stop()        # Trailing stop logic
  └── _sync_state_from_api()         # Reconcile avec Binance
```

---

### C-16 — TypedDict pour `bot_state`

`bot_state: Dict[str, Any]` cache la structure réelle. 
Un `TypedDict` ou `dataclass` permettrait la vérification statique 
de type (mypy/pyright) et éviterait les `KeyError` silencieux.

---

### C-17 — Migration pickle → JSON

**Note** : Le pickle actuel est protégé par HMAC-SHA256 (confirmé dans 
`state_manager.py`). La migration vers JSON est une amélioration de 
**maintenabilité et de lisibilité**, pas un correctif sécurité urgent.

**Dépendance** : Après C-16 (le schéma TypedDict définit le schéma JSON).

---

## Séquence de déploiement progressif

### Étape 1 — Paper Trading (après Phase 0 validée)

| Paramètre | Valeur |
|-----------|--------|
| `PAPER_TRADING` | `true` |
| Durée | **14 jours minimum** |
| Paires actives | Toutes |
| Capital simulé | 100% du capital cible |

**Métriques à surveiller**
- Sharpe OOS > 0.3 sur les 14j
- Drawdown max < seuil `MAX_DRAWDOWN_PCT` configuré
- 0 erreur `[OOS-BLOCK]` non justifiée
- 0 alerte `[STATE-CRITICAL]`
- 0 position sans stop simulé (`stop_loss_placed: True` dans tous les trades)
- Fréquence des trades cohérente avec le backtest (± 30%)

**Critère de passage** : Les 6 métriques ci-dessus respectées 
sur les 14j complets.

---

### Étape 2 — Production Palier 1 (après Phase 1 validée)

| Paramètre | Valeur |
|-----------|--------|
| `PAPER_TRADING` | `false` |
| Durée | **30 jours minimum** |
| Paires actives | **1 seule paire** (la plus liquide, ex : BTC/USDT) |
| Allocation | **10% du capital cible** |

**Métriques à surveiller**
- Après chaque achat : vérifier côté Binance que le stop est placé 
  (`GET /api/v3/openOrders` → 1 ordre `STOP_LOSS` présent)
- 0 position orpheline (position sans stop dans `bot_state`)
- 0 double-exécution partielle (vérifier `partial_taken_1/2` en fin de journée)
- PnL ≥ `-MAX_DRAWDOWN_PCT` configuré sur les 30j
- 0 log `CRITICAL` non résolu
- Latence ordres < 2s (vérifier les timestamps logs)

**Critère de passage** : 30j sans incident critique 
(0 position orpheline, 0 double-exécution, PnL dans les bornes).

---

### Étape 3 — Configuration Cible (après Phase 2 validée)

| Paramètre | Valeur |
|-----------|--------|
| `PAPER_TRADING` | `false` |
| Paires actives | Toutes |
| Allocation | **100% du capital** |

**Pré-requis impératifs**
- [ ] C-06 validé (tests exchange client ≥ 70%)
- [ ] C-12 validé (tests trading engine ≥ 60%)
- [ ] C-09 validé (stop multiplier unifié)
- [ ] C-10 validé (backtest next_open)
- [ ] 30j de palier 1 sans incident

---

## Tableau de synthèse global

| ID | Phase | Description | Fichier:Ligne | Sévérité | Effort | Bloquant prod ? |
|----|-------|-------------|---------------|----------|--------|-----------------|
| C-01 | P0 | Paper trading guard | `exchange_client.py` | P0 | 3h | ⛔ OUI |
| C-02 | P0 | STOP_LOSS_LIMIT → STOP_LOSS | `exchange_client.py:478` | P0 | 2h | ⛔ OUI |
| C-03 | P0 | Supprimer fallback OOS full-sample | `MULTI_SYMBOLS.py:2194` | P0 | 2h | ⛔ OUI |
| C-04 | P1 | Escalade CRITICAL sur load_bot_state | `MULTI_SYMBOLS.py:355` | P1 | 2h | ⛔ OUI |
| C-05 | P1 | Purge oos_blocked conditionnelle | `MULTI_SYMBOLS.py:369` | P1 | 1h | ⛔ OUI |
| C-06 | P1 | Tests exchange_client.py | `tests/` | P1 | 8h | ⛔ OUI |
| C-07 | P1 | Tests position_sizing.py | `tests/` | P1 | 6h | ⛔ OUI |
| C-08 | P1 | Tests state_manager.py | `tests/` | P1 | 4h | ⛔ OUI |
| C-09 | P2 | Stop multiplier hardcodé → config | `MULTI_SYMBOLS.py:1342` | P2 | 30min | ℹ️ Palier 2 |
| C-10 | P2 | Backtest next_open | Backtest engine | P2 | 4h | ℹ️ Palier 2 |
| C-11 | P2 | Tests error_handler.py | `tests/` | P2 | 3h | ℹ️ Palier 2 |
| C-12 | P2 | Tests _execute_real_trades_inner | `tests/` | P2 | 12h | ℹ️ Palier 2 |
| C-13 | P2 | Helper _get_coin_balance() | `MULTI_SYMBOLS.py:1003` | P2 | 1h | ℹ️ Palier 2 |
| C-14 | P3 | Expanding window indicateurs | Backtest engine | P3 | 1 sem | ❌ NON |
| C-15 | P3 | Refactoring _execute_real_trades_inner | `MULTI_SYMBOLS.py:984` | P3 | 1 sem | ❌ NON |
| C-16 | P3 | TypedDict pour bot_state | `MULTI_SYMBOLS.py:241` | P3 | 3j | ❌ NON |
| C-17 | P3 | Migration pickle → JSON | `state_manager.py` | P3 | 2j | ❌ NON |

**Total effort estimé**
- Phase 0 : 7h
- Phase 1 : 21h
- Phase 2 : 20h + 5h30
- Phase 3 : ~3 semaines
- **Total bloquant prod** : **~28h** (Phase 0 + Phase 1)

---

## Arbre des dépendances

```
C-01 (paper guard)
  └── C-06 (tests exchange_client)
        └── C-12 (tests trading engine)
              └── C-15 (refactoring — APRÈS tests)
                    └── C-16 (TypedDict)
                          └── C-17 (JSON migration)

C-02 (STOP_LOSS type)
  └── (indépendant, mais même fichier que C-01)

C-03 (OOS fallback supprimé)
  └── C-05 (purge oos_blocked conditionnelle)

C-04 (load_bot_state CRITICAL)
  └── C-08 (tests state_manager)

C-07 (tests position_sizing)
  └── (indépendant)

C-09 (stop multiplier)
  └── (indépendant — 30min, appliquer tôt)

C-10 (backtest next_open)
  └── C-14 (expanding window — APRÈS C-10)

C-11 (tests error_handler)
  └── (indépendant)

C-13 (helper balance)
  └── (indépendant)
```

---

*Plan généré sur la base de l'audit technique du 03 mars 2026.*  
*Aucun fichier de code n'a été modifié lors de la génération de ce document.*