# PLAN D'ACTION — Fix A-3 Cooldown Timeframe Loss on Restart

**Priorité** : 🔴 CRITIQUE (protection capital — cooldown de 12 jours réduit silencieusement à 12h)  
**Création** : 2026-05-04 à 15:06  
**Origine** : Analyse log_bot.md session 14:11–14:45 (log line 34)  
**Statut** : ✅ TERMINÉ

---

## Diagnostic

### Symptôme observé
```
[A-3 REVALIDATE] PEPEUSDC — cooldown corrigé: 594 min restantes (était 17155 min)
```
Au restart, le `position_reconciler` a "corrigé" un cooldown de 12 jours (17155 min) à 12 heures
(594 min). La valeur initiale était **correcte**.

### Cause racine
Le cooldown A-3 est calculé en `n_candles × durée_timeframe_de_la_stratégie`.  
Pour PEPE (stratégie 1d) : `12 × 86400s = 12 jours`.

Au restart, `position_reconciler.py` recompute le "cooldown correct" avec :
```python
_tf = _ps.get('entry_timeframe') or '1h'   # ← PROBLÈME : entry_timeframe a été effacé
```
Après un SL, `entry_timeframe` est remis à `None` par le reset d'état dans
`update_pair_state(... entry_timeframe=None ...)`. Le reconciler tombe donc sur le
fallback `'1h'` et calcule `12 × 3600s = 12 heures` au lieu de `12 × 86400s = 12 jours`.

### Conséquence
- PEPE 1d : cooldown réel = 12 jours, cooldown après restart = 12 heures → **re-entrée prématurée de ~11 jours**
- SOL 4h : cooldown réel = 48 heures, cooldown après restart = 12 heures → **re-entrée prématurée de 36h**

### Solution retenue — Stocker `_sl_cooldown_timeframe` au moment du set

**Principe** : Quand on écrit `_stop_loss_cooldown_until`, écrire simultanément
`_sl_cooldown_timeframe = ctx.time_interval`. Le reconciler lit cette clé au lieu
de `entry_timeframe` (qui peut être None). Minimal, chirurgical, cohérent backtest/live.

---

## Fichiers à modifier

| # | Fichier | Changement |
|---|---------|------------|
| 1 | `code/src/MULTI_SYMBOLS.py` | Ajouter `_sl_cooldown_timeframe: str` dans `PairState` |
| 2 | `code/src/state_manager.py` | Ajouter `'_sl_cooldown_timeframe'` dans `_KNOWN_PAIR_KEYS` |
| 3 | `code/src/order_manager.py` | 2 emplacements : set `_sl_cooldown_timeframe` avec `_stop_loss_cooldown_until` |
| 4 | `code/src/position_reconciler.py` | Utiliser `_ps.get('_sl_cooldown_timeframe') or ...` au lieu de `_ps.get('entry_timeframe')` |

---

## Étapes détaillées

### ✅ Étape 1 — `MULTI_SYMBOLS.py` : PairState TypedDict

**Localiser** : autour de la ligne `_stop_loss_cooldown_until: float` (section `# Cooldown post-stop (A-3)`)

**Modifier** :
```python
# --- Cooldown post-stop (A-3) ---
_stop_loss_cooldown_until: float
_sl_cooldown_timeframe: str          # ← AJOUTER : timeframe de la stratégie au moment du SL
```

---

### ✅ Étape 2 — `state_manager.py` : _KNOWN_PAIR_KEYS

**Localiser** : la ligne `'_stop_loss_cooldown_until',` dans `_KNOWN_PAIR_KEYS`

**Modifier** :
```python
'_stop_loss_cooldown_until',
'_sl_cooldown_timeframe',          # ← AJOUTER juste après
```

---

### ✅ Étape 3 — `order_manager.py` : 2 emplacements de set

**Emplacement 3a — `_handle_exchange_sl_fill`** (autour de la L796-799) :
```python
    # A-3: cooldown post-stop-loss
    _cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
    if _cd_candles > 0:
        _candle_sec = TIMEFRAME_SECONDS.get(ctx.time_interval, 3600)
        ps['_stop_loss_cooldown_until'] = time.time() + (_cd_candles * _candle_sec)
        ps['_sl_cooldown_timeframe'] = ctx.time_interval       # ← AJOUTER
        logger.info(
            "[A-3 COOLDOWN] Post-stop-loss exchange: %d bougies x %ds = %.1fh",
            _cd_candles, _candle_sec, (_cd_candles * _candle_sec) / 3600,
        )
```

**Emplacement 3b — `_handle_manual_sl_trigger` / safe_market_sell** (autour de L955-958) :
```python
            # A-3: cooldown post-stop-loss
            _cd_candles = getattr(config, 'stop_loss_cooldown_candles', 0)
            if _cd_candles > 0:
                _candle_sec = TIMEFRAME_SECONDS.get(ctx.time_interval, 3600)
                ps['_stop_loss_cooldown_until'] = time.time() + (_cd_candles * _candle_sec)
                ps['_sl_cooldown_timeframe'] = ctx.time_interval   # ← AJOUTER
                logger.info(
                    "[A-3 COOLDOWN] Post-stop-loss : %d bougies x %ds = %.1fh",
                    _cd_candles, _candle_sec, (_cd_candles * _candle_sec) / 3600,
                )
```

> **Note** : L'emplacement 3c (L1135-1141, `_handle_exchange_sl_fill` SL-reconcile) utilise déjà
> `ctx.time_interval` pour `_candle_sec` — ajouter de même `ps['_sl_cooldown_timeframe'] = ctx.time_interval`
> juste après le set de `_stop_loss_cooldown_until`.

---

### ✅ Étape 4 — `position_reconciler.py` : lecture de la clé correcte

**Localiser** : le bloc de revalidation A-3 dans la branche `else` (position cohérente),
autour de la ligne :
```python
_tf = _ps.get('entry_timeframe') or '1h'
```

**Modifier** :
```python
_tf = _ps.get('_sl_cooldown_timeframe') or _ps.get('entry_timeframe') or '1h'
```

Priorité de lookup :
1. `_sl_cooldown_timeframe` — timeframe exact sauvegardé au moment du SL (idéal)
2. `entry_timeframe` — fallback si migration depuis ancienne version de state
3. `'1h'` — dernier recours

---

### ✅ Étape 5 — Validation syntaxe + tests

**Résultat** : 807 passed in 47.00s ✅

```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('MULTI_SYMBOLS OK')"
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/state_manager.py').read()); print('state_manager OK')"
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/order_manager.py').read()); print('order_manager OK')"
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/position_reconciler.py').read()); print('position_reconciler OK')"
pytest tests/ -x -q
```

---

### ✅ Étape 6 — Mise à jour `tasks/lessons.md`

Ajouter **L-29** :

> **L-29** · 2026-05-04 · `entry_timeframe` effacé après reset d'état post-SL → toujours
> stocker `_sl_cooldown_timeframe` au moment du set du cooldown. Ne jamais recalculer un
> cooldown depuis `entry_timeframe` dans le reconciler car cette clé peut être `None`.

---

## Critères d'acceptation

- [ ] `position_reconciler` ne modifie plus la valeur de `_stop_loss_cooldown_until` quand le
  state contient `_sl_cooldown_timeframe` correct
- [ ] Un cooldown PEPE 1d (12 jours) survit à un restart sans être tronqué
- [ ] Un cooldown SOL 4h (48h) survit à un restart sans être tronqué
- [ ] `pytest tests/ -x -q` : 0 failed
- [ ] Ruff + Pyright : 0 erreur

---

## Hors périmètre (décision délibérée)

- **ANOMALIE 2 (STOCH-OPT restart)** : comportement intentionnel, réoptimisation à chaque
  démarrage. Pas de bug.
- **PEPE WF systématiquement FAIL → fallback IS** : risque d'overfit connu, déjà documenté
  dans L-13. Nécessite une analyse dédiée séparée.
- **SL-MANQUANT detect sans re-place** : gap connu (session précédente), hors périmètre.
