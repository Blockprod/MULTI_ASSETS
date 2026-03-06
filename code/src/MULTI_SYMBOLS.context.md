# MULTI_SYMBOLS.py — Contexte module

## Rôle
**Orchestrateur principal** du bot. Point d'entrée unique (`python MULTI_SYMBOLS.py`). Contient la boucle `scheduler`, le lifecycle du bot (démarrage, arrêt gracieux), et coordonne tous les modules.

## Responsabilités
1. **Initialisation** : `Config.from_env()`, connexion exchange, chargement état, preload OHLCV
2. **Scheduler** : `schedule.every(1).hours.do(run_cycle)` — aligné sur la clôture des bougies 1h
3. **Orchestration par paire** : lance un thread par paire avec `_pair_execution_locks[pair]`
4. **Lifecycle** : démarrage propre, arrêt via SIGINT/SIGTERM, `emergency_halt` global
5. **Resync périodique** : horloge toutes les 30min, réconciliation positions si nécessaire

## WF_SCENARIOS — définis ici, utilisés partout
```python
WF_SCENARIOS = {
    "StochRSI": {...},
    "StochRSI_SMA200": {...},
    "StochRSI_SMA200_ADX": {...},
    "StochRSI_SMA200_ADX_TRIX": {...}
}
```
Ces scénarios sont la **source de vérité**. Toute modification impacte backtest et signaux live.

## Wrappers et globals injectés
- Les fonctions de `trade_helpers`, `signal_generator`, `state_manager` reçoivent des références au `bot_state` global et aux locks via les wrappers définis ici
- **Ne jamais** importer `bot_state` directement depuis un autre module — passer par les APIs exposées dans `state_manager`

## `bot_state` — structure globale
- Dict Python en mémoire, persisté via `state_manager.save_bot_state()`
- Accès uniquement sous `_bot_state_lock`
- Clés top-level : `emergency_halt`, `daily_loss_usdc`, `daily_loss_date`, `pairs`
- Clés par paire : voir `state_manager.context.md` et `_KNOWN_PAIR_KEYS`

## Locks définis ici (globaux)
| Lock | Type | Protège |
|------|------|---------|
| `_bot_state_lock` | RLock | Toutes les lectures/écritures `bot_state` |
| `_pair_execution_locks` | dict[str, Lock] | Exécution concurrente par paire |
| `_oos_alert_lock` | Lock | `_oos_alert_last_sent` (dédoublonnage alertes) |

## Lifecycle du cycle horaire
```python
def run_cycle():
    # 1. Checks globaux (emergency_halt, daily_loss)
    # 2. Pour chaque paire : lancer thread avec _pair_execution_locks[pair]
    # 3. Chaque thread : fetch → signal → size → execute → persist
    # 4. Après tous les threads : save_bot_state(force=True)
    # 5. heartbeat.json mis à jour
```

## Variables critiques à ne pas toucher
- `emergency_halt` : seul `state_manager` ou le signal SIGTERM doit le passer à `True`
- `daily_loss_usdc` : mis à jour **uniquement** après un SELL confirmé
- `_fresh_start_date()` : **ne jamais** remplacer par une date figée dans le code

## Taille du fichier
~3400 lignes — ne pas ajouter de logique métier ici. Toute nouvelle fonctionnalité va dans son module dédié (`trade_helpers`, `signal_generator`, etc.).

## À ne jamais faire
- Importer `bot_state` directement depuis un autre module
- Modifier `WF_SCENARIOS` sans mettre à jour les tests correspondants
- Lancer des opérations exchange hors du contexte `_pair_execution_locks[pair]`
- Appeler `save_bot_state()` sans `force=True` après un trade exécuté
