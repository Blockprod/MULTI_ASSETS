---
creation: 2026-05-07 à 16:26
status: completed
source: analyse exhaustive trading_bot.log (2026-05-07 13:18–15:58, 1381 lignes)
commit: à faire
---

# Plan d'action — Fix BUG-1 (DUST→BUY) + BUG-2 (SL-POLL inactif)

> Origine : analyse ligne par ligne du log `code/logs/trading_bot.log` du 2026-05-07.
> Bug rapporté : "Une position était en cours. La vente partielle n°1 s'est bien exécutée.
> Pourtant, un ordre d'achat s'est exécuté alors que la position précédente n'était toujours
> pas entièrement fermée."
> Deux bugs critiques identifiés, corrigés et validés dans cette session.

---

## Tableau de synthèse

| # | Item | Priorité | Statut | Fichiers |
|---|------|----------|--------|---------|
| 1 | BUY immédiat après reset dust BUY→SELL dans le même cycle | 🔴 CRITIQUE | ✅ Corrigé | `order_manager.py`, `MULTI_SYMBOLS.py` |
| 2 | SL-POLL inactif : `sl_exchange_placed` jamais persisté au démarrage | 🔴 CRITIQUE | ✅ Corrigé | `position_reconciler.py` |

---

## BUG-1 🔴 — BUY dans le même cycle que le reset dust

### Chronologie dans le log

```
15:48:15,908  [PARTIAL-CHECK] PARTIAL-1 detecte : 0.68700000 SOL a 90.26 USDC (~50%)
15:48:15,910  [DUST P0-BUY] SOLUSDC — RESET last_order_side BUY→SELL (balance=0.00055111,
              notional=0.05, min_notional=10.00)
15:48:16,170  [CAPITAL] Dernier BUY trouve a 2026-05-06 18:00:41   ← référence changée
15:48:16,171  [CAPITAL] 5 ventes trouvees depuis dernier BUY = 122.72 USDC
15:48:20,936  Market buy placed: SOLUSDC quote=120.22                ← BUY 5s après le reset
15:48:24,299  [SL-ORDER] Stop-loss exchange placé: SOLUSDC qty=1.355
```

Le SL avait été exécuté par Binance entre 13:18 et 15:48 (non détecté en live → voir BUG-2).
Le dust restant (0.00055111 SOL = 0.05 USDC < 10 USDC min_notional) déclenche le handler
dust qui remet `last_order_side='SELL'`. Dans la foulée, le cycle continue et `_execute_buy`
est appelé car `position_has_crypto=False` — la garde P0-BUY ne bloque plus car l'état
vient d'être réinitialisé.

### Cause racine

`_handle_dust_cleanup` (order_manager.py ~L1267) retournait `False` dans tous les cas de
reset de champs stales (qu'il y ait eu un reset BUY→SELL ou non). Le caller
(`MULTI_SYMBOLS.py::execute_real_trades`) ne pouvait pas distinguer "pas de position"
de "on vient de réinitialiser une position — skip ce cycle".

```python
# AVANT (order_manager.py) — chemin "dust intradable, last_order_side=BUY"
if _stale_fields:
    logger.critical("[DUST P0-BUY] ...")
    ps.update({..., 'last_order_side': 'SELL', ...})
    deps.save_fn(force=True)
    logger.info("[DUST] État pair_state réinitialisé ...")
# ← PAS de return → la fonction continue et retourne position_has_crypto=False
# ← L'appelant appelle alors _execute_buy immédiatement
```

```python
# AVANT (MULTI_SYMBOLS.py)
position_has_crypto = _handle_dust_cleanup(ctx, deps)
if position_has_crypto:          # False → _execute_buy
    _execute_signal_sell(ctx, deps)
else:
    _execute_buy(ctx, deps)      # ← BUY déclenché 5s après le reset
```

### Fix appliqué

**`order_manager.py` — `_handle_dust_cleanup`** : changer la signature de retour de
`bool` en `Optional[bool]`. Retourner `None` (sentinel) quand un reset de champs stales
vient de se produire.

```python
# APRÈS (order_manager.py)
def _handle_dust_cleanup(ctx: '_TradeCtx', deps: '_TradingDeps') -> Optional[bool]:
    """...
    Retourne None si un reset dust BUY→SELL vient de se produire dans ce cycle
    (le caller doit skipper le cycle entier — pas d'achat immédiat).
    """
    ...
    if _stale_fields:
        logger.critical("[DUST P0-BUY] ...")
        ps.update({..., 'last_order_side': 'SELL', ...})
        deps.save_fn(force=True)
        logger.info("[DUST] État pair_state réinitialisé ...")
        return None   # ← sentinel : "cycle à skipper"
```

**`MULTI_SYMBOLS.py` — `execute_real_trades`** : intercepter le sentinel `None`.

```python
# APRÈS (MULTI_SYMBOLS.py)
position_has_crypto = _handle_dust_cleanup(ctx, deps)
if position_has_crypto is None:
    logger.info("[DUST P0-BUY] %s — cycle skipé après reset dust.", backtest_pair)
    return
if position_has_crypto:
    _execute_signal_sell(ctx, deps)
else:
    _execute_buy(ctx, deps)
```

### Impact

- Aucun BUY ne peut plus être déclenché dans le même cycle qu'un reset dust P0-BUY.
- Le cycle suivant (2 min plus tard), `last_order_side='SELL'` est persisté → P0-BUY
  ne s'applique plus et le BUY peut légitimement être évalué si les conditions le justifient.
- Compatibilité descendante : `Optional[bool]` — `None` est falsy, donc les éventuels
  anciens callers qui faisaient `if position_has_crypto:` continuent de fonctionner
  (ils traitent `None` comme `False`). Le check explicite `is None` dans MULTI_SYMBOLS.py
  assure le comportement correct.

---

## BUG-2 🔴 — SL-POLL inactif toute la session

### Chronologie dans le log

```
13:18:xx  [RECONCILE C-11] Stop-loss déjà actif sur Binance pour SOLUSDC ✓
          ← sl_exchange_placed NON écrit dans pair_state

13:20 → 15:48  0 entrée [SL-POLL] sur 1381 lignes de log (2h30)
          ← condition sl_exchange_placed==True jamais vraie

~13:18–15:48  SL fill Binance (non détecté)
          ← si SL-POLL avait tourné, il aurait détecté le fill dans l'heure
```

### Cause racine

`position_reconciler.py` (L479–485) — branch C-11 "SL déjà actif" :

```python
# AVANT
else:
    logger.info(
        "[RECONCILE C-11] Stop-loss déjà actif sur Binance pour %s ✓",
        backtest_pair,
    )
# ← aucune écriture dans bot_state
# ← sl_exchange_placed reste à False (ou None) tel que chargé depuis le fichier persisté
```

La condition SL-POLL dans `MULTI_SYMBOLS.py` :
```python
if (ps.get('sl_exchange_placed') is True       # ← jamais True
    and ps.get('sl_order_id') is not None
    and ps.get('last_order_side') == 'BUY'):
```

Le `sl_exchange_placed=True` n'est écrit que dans le branch "SL reposé" (L455–470) —
quand le SL manquait sur Binance et a été reposé. Le branch "SL déjà là" était muet.

### Fix appliqué

**`position_reconciler.py`** — branch `else` (SL déjà actif) : lire l'orderId depuis
`open_orders` et persister `sl_exchange_placed=True` + `sl_order_id`.

```python
# APRÈS
else:
    _existing_sl = next(
        (o for o in open_orders if o.get('type', '') in stop_types),
        None,
    )
    if _existing_sl:
        with deps.bot_state_lock:
            _ps_sl = deps.bot_state.setdefault(backtest_pair, {})
            _ps_sl['sl_exchange_placed'] = True
            _ps_sl['sl_order_id'] = _existing_sl.get('orderId')
        deps.save_fn(force=True)
        logger.info(
            "[RECONCILE C-11] Stop-loss déjà actif sur Binance pour %s ✓ "
            "(orderId=%s, sl_exchange_placed=True persisté)",
            backtest_pair, _existing_sl.get('orderId'),
        )
    else:
        logger.info(
            "[RECONCILE C-11] Stop-loss déjà actif sur Binance pour %s ✓",
            backtest_pair,
        )
```

### Impact

- Dès le démarrage, si un SL est actif sur Binance, `sl_exchange_placed=True` et
  `sl_order_id` sont persistés dans `bot_state` et sauvegardés sur disque.
- SL-POLL se déclenche dès le premier cycle (cycle 30 → ~1h) et surveille le fill.
- Si le SL est exécuté pendant une session longue, il sera détecté en moins d'une heure
  au lieu d'attendre le prochain redémarrage.

---

## Validation

```
✅ Syntaxe Python — order_manager.py    → ast.parse OK
✅ Syntaxe Python — MULTI_SYMBOLS.py   → ast.parse OK
✅ Syntaxe Python — position_reconciler.py → ast.parse OK
✅ Tests pytest — 807/807 passed (44.48s)
```

---

## Fichiers modifiés

| Fichier | Lignes concernées | Changement |
|---------|-------------------|------------|
| `code/src/order_manager.py` | `_handle_dust_cleanup` ~L1267 | Signature `bool` → `Optional[bool]`, `return None` après reset |
| `code/src/MULTI_SYMBOLS.py` | `execute_real_trades` ~L1479 | `if position_has_crypto is None: return` |
| `code/src/position_reconciler.py` | C-11 branch `else` ~L479 | Écriture `sl_exchange_placed=True` + `sl_order_id` + `save_fn(force=True)` |
| `tasks/lessons.md` | L-34, L-35 | Deux nouvelles leçons ajoutées |

---

## Leçons enregistrées

- **L-34** : DUST reset BUY→SELL suivi d'un BUY dans le même cycle → `_handle_dust_cleanup`
  doit retourner `None` pour signaler "skip cycle entier".
- **L-35** : `position_reconciler.py` C-11 branch "SL déjà actif" ne persistait pas
  `sl_exchange_placed` → SL-POLL inactif toute la session.
