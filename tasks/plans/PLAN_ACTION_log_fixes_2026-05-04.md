---
creation: 2026-05-04 à 13:34
status: todo
source: analyse log_bot.md session 2026-05-04 11:03→13:20
---

# Plan d'action — Corrections issues log 2026-05-04

> Source : analyse du log `log_bot.md` du 2026-05-04.
> 3 anomalies identifiées, classées par priorité.

---

## P1 — Bug d'affichage SL (`.4f` → `0.0000` sur prix < 0.01)

### Symptôme
```
Stop-Loss utilise : 0.0000 USDC (fixe à l'entrée)
```
`%.4f` appliqué à `3.3888378e-06` donne `0.0000`.
Idem pour les prix trailing si activé sur PEPE.

### Localisation
- `code/src/order_manager.py` ligne ~832 et ~1013 :
  ```python
  stop_loss_info = f"{stop_loss_fixed:.4f} USDC (fixe à l'entrée)"
  # et
  stop_loss_info = f"{trailing_stop:.4f} USDC (dynamique : trailing)"
  ```

### Correction
Remplacer le format `.4f` par `:.8g` (notation scientifique automatique quand
nécessaire) — le même format déjà utilisé dans `display_closure_panel` pour
`current_price` et dans les panels de signal de vente.

```python
# AVANT
stop_loss_info = f"{stop_loss_fixed:.4f} USDC (fixe à l'entrée)"
# APRÈS
stop_loss_info = f"{stop_loss_fixed:.8g} USDC (fixe à l'entrée)"

# AVANT
stop_loss_info = f"{trailing_stop:.4f} USDC (dynamique : trailing)"
# APRÈS
stop_loss_info = f"{trailing_stop:.8g} USDC (dynamique : trailing)"
```

**Fichiers à modifier :** `code/src/order_manager.py` (3 occurrences : ~L832, ~L1013, ~L1177)

**Tests à ajouter :** vérifier que `stop_loss_info` contient la valeur non-nulle
pour un prix < 0.01 dans `tests/test_trade_helpers.py`.

---

## P2 — Prix fill réel SL non loggé (prix affiché ≠ prix d'exécution Binance)

### Symptôme
Au moment de la détection du SL exchange-filled :
```
Prix actuel : 3.97e-06 USDC    ← prix du cycle de polling, pas du fill
```
Le SL Binance `STOP_LOSS_LIMIT` a été exécuté à un prix inconnu (probablement
autour de 3.3888e-06 au moment du spike baissier). Le journal de trade utilise
`_fill_price` récupéré via `get_order`, mais le panel d'affichage utilise
`ctx.current_price` (prix au moment de la vérification 2 min plus tard).

### Localisation
`code/src/order_manager.py` — `_handle_exchange_sl_fill()` :
- Le `_fill_price` est déjà récupéré via `get_order` → utilisé dans `log_trade`
- `display_closure_panel` reçoit `ctx.current_price` au lieu de `_fill_price`

### Correction
Passer `_fill_price` (ou le prix de l'ordre récupéré) à `display_closure_panel`
en paramètre additionnel, et l'afficher en complément du prix courant.

```python
# Dans _handle_exchange_sl_fill — après récupération de _fill_price
display_closure_panel(
    stop_loss_info,
    current_price=ctx.current_price,
    fill_price=_fill_price,          # ← NOUVEAU
    coin_symbol=ctx.coin_symbol,
    coin_balance=ctx.coin_balance,
    console=deps.console,
)
```

```python
# Dans display_ui.py — display_closure_panel
def display_closure_panel(
    stop_loss_info: str, current_price: float,
    coin_symbol: str, coin_balance: float, console: Console,
    fill_price: Optional[float] = None,   # ← NOUVEAU (optionnel)
):
    ...
    if fill_price is not None:
        closure_grid.add_row("Prix fill Binance", f"{fill_price:.8g} USDC")
    closure_grid.add_row("Prix courant (polling)", f"{current_price:.8g} USDC")
```

**Fichiers à modifier :**
- `code/src/display_ui.py` : signature `display_closure_panel` + nouvelle ligne d'affichage
- `code/src/order_manager.py` : appel dans `_handle_exchange_sl_fill` (~L834)

**Note :** Les autres appels à `display_closure_panel` (_handle_manual_sl_trigger,
_reconcile_zero_balance_sl) n'ont pas de `fill_price` disponible → laisser
`fill_price=None` par défaut, la ligne n'apparaît pas.

---

## P3 — Indicateurs SOL figés sur 2h+ (EMA/ADX identiques cycle après cycle)

### Symptôme
De 11:04 à 13:20 (>2h), les valeurs suivantes sont **strictement constantes** :
```
EMA30=84.19311832  EMA60=84.49399225  ADX=20.29
```
Sur un timeframe 4h, une bougie dure 240 min — l'EMA ne devrait changer que
lorsqu'une nouvelle bougie 4h se ferme. Cela peut donc être **normal**
(bougie 4h ouverte à 11:00, ferme à 15:00). Mais à confirmer à 15:00.

### Observation à faire
- Si les valeurs changent à 15:00:xx → **comportement normal**, pas de bug.
- Si les valeurs restent identiques après 15:00 → **bug de cache ou de
  recalcul**, à investiguer dans `data_fetcher.py` / `indicators_engine.py`.

### Action immédiate
- **Ne rien modifier** pour l'instant.
- Observer le prochain cycle backtest+WF planifié (~12:04 + 1h = ~13:04 déjà
  passé, log finit à 13:20 avec les mêmes valeurs).
- **Créer un log de surveillance** : ajouter un log INFO dans
  `indicators_engine.py` ou `order_manager.py` qui trace la date de la
  dernière bougie fermée utilisée pour le calcul, afin de confirmer que
  `shift(1)` s'applique correctement sur les données 4h.

```python
# À ajouter dans indicators_engine.py après calcul EMA/ADX
logger.info(
    "[IND] %s %s — dernière bougie fermée: %s (close=%.6g)",
    pair, timeframe, df.index[-2].isoformat(), df['close'].iloc[-2]
)
```

**Fichiers à modifier (si bug confirmé) :**
- `code/src/indicators_engine.py`
- `code/src/data_fetcher.py` (vérifier TTL cache bougie partielle)

---

## Ordre d'exécution recommandé

| # | Priorité | Fix | Effort | Risque |
|---|----------|-----|--------|--------|
| 1 | P1 | Format `:.8g` pour stop_loss_info dans closure panel | 10 min | nul |
| 2 | P2 | Affichage prix fill Binance dans closure panel | 30 min | faible |
| 3 | P3 | Observer indicateurs SOL à 15h00 — corriger si toujours figés | variable | faible |

---

## Validation après correction

```powershell
# AST check
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/order_manager.py').read()); print('order_manager OK')"
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/display_ui.py').read()); print('display_ui OK')"

# Tests complets
.venv\Scripts\pytest.exe tests/ -x -q
```
