# PLAN D'ACTION — MULTI_ASSETS — 2026-03-19
Sources : `tasks/audits/AUDIT_STRATEGIQUE_MULTI_ASSETS.md`
Total : 🔴 0 · 🟠 1 · 🟡 2 · Effort estimé : ~3.5h

---

## PHASE 1 — CRITIQUES 🔴

> Aucune correction critique identifiée dans cet audit.

---

## PHASE 2 — MAJEURES 🟠

### [ST-P1-01] Guard positions longues simultanées (max_concurrent_long)

**Fichier :** `code/src/bot_config.py:105` · `code/src/MULTI_SYMBOLS.py:~1050`  
**Problème :**  
Aucune limite sur le nombre de positions longues simultanées. En conditions de marché corrélées (crash généralisé, risk-off crypto), le bot peut ouvrir N positions sur des paires toutes longues avec des corrélations intra-classe > 0.7. L'exposition effective devient `N × position_size`, dépassant potentiellement `daily_loss_limit_pct`. Ce dernier est réactif (déclenche après pertes réalisées) — aucun guard préventif n'existe.

**Correction :**  
**Étape A — `bot_config.py`** : Ajouter le champ `max_concurrent_long: int = 4` après `max_drawdown_pct` (ligne ~105). Ajouter le chargement `.env` dans `from_env()` : `config_data['max_concurrent_long'] = int(os.getenv('MAX_CONCURRENT_LONG', '4'))`.

**Étape B — `MULTI_SYMBOLS.py`** : Ajouter le guard dans `_execute_real_trades_inner()`, après le bloc `emergency_halt` (ligne ~1050) et avant le `setdefault(backtest_pair, {})`. Le guard compte toutes les paires avec `last_order_side == 'BUY'` dans `bot_state`, hors la paire courante. Si ce compte atteint `config.max_concurrent_long`, retourner sans achat avec `logger.info`.

```python
# Insérer après le bloc emergency_halt check (~L1051), avant pair_state = ...
with _bot_state_lock:
    _open_longs = [
        p for p, s in bot_state.items()
        if isinstance(s, dict) and s.get('last_order_side') == 'BUY'
    ]
if len(_open_longs) >= config.max_concurrent_long:
    logger.info(
        "Max positions longues atteint (%d/%d) — achat bloqué pour %s",
        len(_open_longs), config.max_concurrent_long, backtest_pair,
    )
    return
```

**Note :** Le guard ne bloque PAS la gestion des positions existantes (trailing/SL/partials) — il ne s'applique qu'à l'ouverture d'une nouvelle position (la vérification est avant le code d'achat, pas avant la boucle principale).

**Validation :**
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/bot_config.py').read()); print('OK')"
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : 590/590 pass. Vérifier que `test_trading_engine.py` et `test_p0_fixes.py` passent intégralement.

**Dépend de :** Aucune  
**Statut :** ✅ 2026-03-19

---

## PHASE 3 — MINEURES 🟡

### [ST-P2-02] Persistance du flag drawdown_halted dans bot_state

**Fichier :** `code/src/MULTI_SYMBOLS.py:~1150` · `code/src/state_manager.py:54` · `code/src/MULTI_SYMBOLS.py:~257`  
**Problème :**  
Le guard drawdown (EM-P2-05, `MULTI_SYMBOLS.py:~1150`) envoie une alerte email quand le drawdown non réalisé dépasse `max_drawdown_pct`. Cependant, aucun flag n'est persisté dans `bot_state`. Si le bot redémarre mid-drawdown, la protection ne s'applique qu'au prochain cycle où `current_price` est recalculé — ce qui est correct puisque le calcul est dynamique. La vraie lacune est l'absence d'action corrective (vente d'urgence) et l'absence de traçabilité de l'événement dans l'état persisté.

**Correction :**  
**Étape A — `state_manager.py:54`** : Ajouter `'drawdown_halted'` à `_KNOWN_PAIR_KEYS` pour éviter les warnings de clé inconnue.

**Étape B — `MULTI_SYMBOLS.py` — PairState TypedDict** : Ajouter `drawdown_halted: Optional[bool]` dans le TypedDict `PairState` (~L257).

**Étape C — `MULTI_SYMBOLS.py:~1158`** : Dans le bloc EM-P2-05, après le throttle, persister `pair_state['drawdown_halted'] = True` et appeler `save_bot_state(bot_state)`. À la condition de BUY (avant exécution d'achat), ajouter un check `if pair_state.get('drawdown_halted'): return` — ce flag est réinitialisé lors d'une vente (dans `_update_daily_pnl` ou après `SELL` confirmé).

**Validation :**
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/MULTI_SYMBOLS.py').read()); print('OK')"
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/state_manager.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : 590/590 pass.

**Dépend de :** Aucune  
**Statut :** ✅ 2026-03-19

---

### [ST-P2-01] Pré-sélection WF par sharpe_ratio au lieu de final_wallet

**Fichier :** `code/src/walk_forward.py:441` · `code/src/walk_forward.py:446`  
**Problème :**  
`run_walk_forward_validation()` trie les configs full-sample par `final_wallet` pour constituer les top-5 candidats WF. Un config à Sharpe OOS potentiellement supérieur mais profit nominal moindre (ex. durée position plus courte, moins de trades mais plus précis) peut être éliminé avant même d'être soumis aux folds WF. La sélection par `final_wallet` favorise les configs à haute variance qui ont "eu de la chance" sur la fenêtre IS complète.

**La mitigation actuelle** (cap top-2 par timeframe + decay gate OOS/IS ≥ 0.15) réduit le risque mais ne l'élimine pas.

**Correction :**  
Remplacer la clé de tri `final_wallet` par `sharpe_ratio` aux deux endroits (`walk_forward.py:441` et `446`). Si `sharpe_ratio` est absent du dict résultat, fallback sur `final_wallet` via `x.get('sharpe_ratio', x.get('final_wallet', 0.0))`.

```python
# L441 — remplacer :
for _r in sorted(full_sample_results, key=lambda x: x.get('final_wallet', 0.0), reverse=True):
# par :
for _r in sorted(full_sample_results, key=lambda x: x.get('sharpe_ratio', x.get('final_wallet', 0.0)), reverse=True):

# L446 — remplacer :
top_configs = sorted(_all_candidates, key=lambda x: x.get('final_wallet', 0.0), reverse=True)[:top_n]
# par :
top_configs = sorted(_all_candidates, key=lambda x: x.get('sharpe_ratio', x.get('final_wallet', 0.0)), reverse=True)[:top_n]
```

**Vérification préalable :** Confirmer que `backtest_runner.py` retourne bien une clé `sharpe_ratio` dans son dict résultat (grep `'sharpe_ratio'`). Si absent, utiliser l'alternative d'augmenter `top_n` de 5 à 8 plutôt que de changer le critère.

**Validation :**
```powershell
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/walk_forward.py').read()); print('OK')"
.venv\Scripts\python.exe -m pytest tests/ -x -q
```
Attendu : 590/590 pass. Vérifier notamment `test_backtest.py` et tout test touchant `walk_forward`.

**Dépend de :** Aucune — mais à exécuter en dernier (impact sur logique WF, risque régressif plus élevé)  
**Statut :** ✅ 2026-03-19

---

## SÉQUENCE D'EXÉCUTION

```
ST-P1-01  →  ST-P2-02  →  ST-P2-01
  (P1)         (P2)          (P2)
```

Détail :
1. **ST-P1-01** — Protection capital, priorité maximale. Modifications indépendantes (bot_config.py + MULTI_SYMBOLS.py). Valider tests.
2. **ST-P2-02** — Persistence état drawdown. Modifications 3 fichiers (state_manager.py, MULTI_SYMBOLS.py). Valider tests.
3. **ST-P2-01** — Changement logique WF — tester après confirmation que `sharpe_ratio` est présent dans les résultats backtest. En dernier car potentiel d'effet de bord sur les tests WF.

---

## CRITÈRES PASSAGE EN PRODUCTION

- [ ] Zéro 🔴 ouvert
- [ ] `pytest tests/ -x -q` : 590/590 pass
- [ ] Zéro credential dans les logs
- [ ] Stop-loss garanti après chaque BUY
- [ ] `max_concurrent_long` vérifié en dry-run multi-paires
- [ ] Paper trading validé 5 jours minimum

---

## TABLEAU DE SUIVI

| ID | Titre | Sévérité | Fichier principal | Effort | Statut | Date |
|----|-------|----------|-------------------|--------|--------|------|
| ST-P1-01 | Guard max_concurrent_long | 🟠 P1 | `MULTI_SYMBOLS.py:~1050` + `bot_config.py:105` | ~2h | ✅ | 2026-03-19 |
| ST-P2-02 | Persistance drawdown_halted | 🟡 P2 | `MULTI_SYMBOLS.py:~1158` + `state_manager.py:54` | ~30min | ✅ | 2026-03-19 |
| ST-P2-01 | WF pré-sélection par sharpe_ratio | 🟡 P2 | `walk_forward.py:441,446` | ~1h | ✅ | 2026-03-19 |
