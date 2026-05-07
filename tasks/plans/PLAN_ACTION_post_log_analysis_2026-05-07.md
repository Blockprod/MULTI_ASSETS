---
creation: 2026-05-07 à 12:10
status: completed
source: analyse profonde trading_bot.log (2026-05-04 23:37 → 2026-05-07 11:23, 27 509 lignes)
---

# Plan d'action — Post-analyse log trading_bot.log (2026-05-07)

> Source : analyse exhaustive ligne par ligne du fichier `code/logs/trading_bot.log` (~58h de run).
> 4 items actifs, 3 clos. Classement par priorité décroissante.

---

## Tableau de synthèse

| # | Item | Priorité | Statut | Fichier |
|---|------|----------|--------|---------|
| 1 | Watchdog relance après SIGINT → trades fantômes | 🔴 CRITIQUE | ✅ Corrigé | `watchdog.py` |
| 2 | SL fill non détecté en temps réel (11h de gap) | 🔴 CRITIQUE | ✅ Corrigé | `MULTI_SYMBOLS.py` |
| 3 | Run 2 crash sans log (~12:17 le 05/05) | 🟠 MAJEUR | ✅ Investigué — kill externe | `watchdog.log` |
| 4 | PEPE OOS auto-disable | 🟠 MAJEUR | ✅ Implémenté (N=6) | `MULTI_SYMBOLS.py` |
| 5 | `sizing_mode = 'baseline'` | 🟡 MOYEN | ✅ Fait | `bot_config.py` |
| 6 | SOL dormant toute la période | 🟡 MOYEN | ✅ Normal | — |
| 7 | Bruit IS-Calmar entre restarts | 🟢 INFO | ✅ Acceptable | — |

---

## Item 1 — Watchdog relance après SIGINT 🔴

### Contexte

Le log montre qu'à `2026-05-05 11:29:47` l'utilisateur a envoyé un SIGINT (arrêt volontaire).
Le watchdog a détecté `process dead` et relancé le bot ~1 min plus tard.
Ce run fantôme (11:30–11:35) a exécuté un BUY PEPE à 11:32:30 et un BUY SOL à 11:33:39
**sans aucune ligne dans `trading_bot.log`**.

### Diagnostic

`watchdog.py::run()` (L.262) :
```python
if not self.is_process_running():
    logger.warning("Bot arrêté détecté (process dead)")
    if not self.restart_bot(reason="process_dead"):   # ← inconditionnel
```

`is_process_running()` retourne `False` aussi bien pour un crash que pour un `sys.exit(0)`
propre (SIGINT). Le `returncode` du processus n'est jamais lu avant de décider de redémarrer.

`MULTI_SYMBOLS.py` distingue SIGINT (`_voluntary_event`) correctement (L.1948-1949)
mais **ne transmet pas cette information au watchdog** — le heartbeat.json ne contient aucun
flag `"voluntary_stop"`.

### Fix

**Approche A — `returncode`** (la plus simple, sans modifier MULTI_SYMBOLS.py) :
Dans `watchdog.py::run()`, lire `self.process.returncode` après avoir confirmé que le
process est mort. Si `returncode == 0` → arrêt propre → ne pas redémarrer.

```python
# watchdog.py — run() — AVANT
if not self.is_process_running():
    logger.warning("Bot arrêté détecté (process dead)")
    if not self.restart_bot(reason="process_dead"):
        ...

# APRÈS
if not self.is_process_running():
    rc = self.process.returncode if self.process else None
    if rc == 0:
        logger.info(
            "[WATCHDOG] Bot terminé proprement (returncode=0) — "
            "pas de redémarrage automatique."
        )
        break  # sortie volontaire du watchdog
    logger.warning("Bot arrêté détecté (process dead, returncode=%s)", rc)
    if not self.restart_bot(reason=f"process_dead (rc={rc})"):
        ...
```

**Approche B — heartbeat flag** (plus robuste, signal explicite) :
MULTI_SYMBOLS.py écrit `"voluntary_stop": true` dans `heartbeat.json` lors d'un SIGINT,
et le watchdog lit ce flag avant de décider de redémarrer.

→ **Recommandation : Approche A** (minimal, sans risque de régression).

### Validation post-fix
```
pytest tests/ -x -q
```
+ vérifier manuellement : Ctrl+C du bot → watchdog ne redémarre pas.

---

## Item 2 — SL fill non détecté en temps réel 🔴

### Contexte

Le SL PEPE a été déclenché à ~00:11 (peu après l'achat de 00:11:11).
Le bot_state est resté `in_position=True` pendant **~11h18min** jusqu'au redémarrage de 11:35.
Pendant ce temps :
- Aucun cooldown A-3 actif → nouveau BUY possible si signal
- Capital PEPE incorrectement comptabilisé comme immobilisé
- Aucun email d'alerte post-SL

### Diagnostic

La détection du SL fill existe **uniquement au redémarrage** dans `position_reconciler.py`
(L.186-219 : `get_order(sl_order_id)` → si `status=FILLED` → reconcile).

Dans la boucle principale de MULTI_SYMBOLS.py, la vérification L.1203-1244 teste
`sl_exchange_placed=False` (SL non encore posé) — différent d'un fill. Aucun polling
du statut d'un SL déjà posé.

### Fix

Ajouter dans `MULTI_SYMBOLS.py` un check périodique dans la boucle principale :
- Toutes ~30 itérations (≈ 1h à 2 min/cycle)
- Si `in_position=True AND sl_order_id is not None AND sl_exchange_placed=True`
- Appeler `get_order(sl_order_id)` → si `status=FILLED` → déclencher la logique post-SL

**Pseudocode** :
```python
# MULTI_SYMBOLS.py — dans la boucle principale (après le check SL manquant)
SL_POLL_INTERVAL = 30  # cycles (~1h)
_sl_poll_counter = getattr(_runtime, 'sl_poll_counter', 0) + 1
setattr(_runtime, 'sl_poll_counter', _sl_poll_counter)

if (
    _sl_poll_counter % SL_POLL_INTERVAL == 0
    and pair_state.get('last_order_side') == 'BUY'
    and pair_state.get('sl_exchange_placed')
    and pair_state.get('sl_order_id')
):
    try:
        _sl_info = client.get_order(
            symbol=real_trading_pair,
            orderId=pair_state['sl_order_id']
        )
        if _sl_info.get('status') == 'FILLED':
            logger.info(
                "[SL-POLL] SL %s FILLED détecté en temps réel pour %s",
                pair_state['sl_order_id'], backtest_pair
            )
            # → appeler handle_sl_fill() ou équivalent
    except Exception as _poll_err:
        logger.debug("[SL-POLL] Erreur polling SL: %s", _poll_err)
```

**Contrainte** : respecter le rate limiter token bucket (18 req/s). Un appel `get_order`
toutes 30 itérations × 2 paires = 2 appels API supplémentaires/heure → négligeable.

### Validation post-fix
```
pytest tests/ -x -q
```

---

## Item 3 — Run 2 crash sans log 🟠

### Contexte

Entre `11:38:06` (dernier cycle run 2) et `12:17:48` (démarrage run 3) = ~39 min,
la run 2 s'est terminée sans aucun log `[SHUTDOWN]`.

### Action

1. Lire `code/logs/watchdog.log` → chercher entrées entre 11:38 et 12:18 le 2026-05-05
2. Si watchdog.log ne couvre pas cette période → cause externe (Windows Task Scheduler,
   Windows Service, OOM, erreur non catchée en dehors du main try/except)
3. Consulter Windows Event Viewer : `eventvwr.msc` → Windows Logs → Application →
   filtrer sur 2026-05-05 11:38–12:18 → chercher PID du process Python

### Critère de clôture

Cause identifiée et documentée dans `tasks/lessons.md`.

---

## Item 4 — PEPE OOS auto-disable 🟠

### Contexte

Sur 58h de log, **0 configuration PEPE** n'a passé les OOS gates (Sharpe ≥ 0.8, WR ≥ 30%).
OOS Sharpe systématiquement négatif (−0.51 à −0.86). Le bot trade PEPE en pur fallback
IS-Calmar depuis le début sans aucune sécurité automatique.

### Ce qui existe déjà

`pair_state.oos_blocked: bool` (MULTI_SYMBOLS.py L.312) — bloque les BUYs pour une paire.
`apply_oos_quality_gate()` (L.1680) — évalue les gates à chaque cycle WF.

**Ce qui manque** : un compteur `oos_fail_streak` qui auto-sette `oos_blocked=True`
après N cycles consécutifs sans config OOS valide.

### Décision requise

Avant implémentation, décider :
- Seuil N (ex: 24 cycles consécutifs = ~24h) ?
- Comportement : bloquer seulement les nouveaux BUYs (position existante non affectée) ?
- Déblocage : manuel uniquement, ou auto si une config OOS passe ?

### Critère de clôture

Décision documentée. Si go : implémentation + tests.

---

## Items clos

### Item 5 — `sizing_mode = 'baseline'` ✅
- Corrigé le 2026-05-07 (cette session).
- `bot_config.py` L.73 : `sizing_mode: str = 'baseline'`
- `bot_config.py` L.201 : `os.getenv('SIZING_MODE', 'baseline')`

### Item 6 — SOL dormant ✅
- Normal : EMA1 ≤ EMA2 sur toute la période → signal d'achat jamais déclenché.
- Aucune action.

### Item 7 — Bruit IS-Calmar ✅
- Acceptable tant qu'aucune config OOS n'est disponible.
- Le fallback IS-Calmar garantit un choix déterministe parmi les configs testées.
