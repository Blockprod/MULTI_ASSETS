# state_manager.py — Contexte module

## Rôle
Unique gestionnaire de la persistance de l'état du bot. Toute lecture/écriture de `bot_state` passe par ce module. Ne jamais lire/écrire `bot_state.json` directement depuis un autre module.

## Format de persistance
```
JSON_V1:<payload_base64>:<hmac_sha256_hex>
```
- Payload = JSON compressé en base64
- Clé HMAC = `BINANCE_SECRET_KEY` (variable d'environnement)
- `StateError` levée sur mismatch HMAC → démarrage avec état vide + réconciliation API

## Clés connues : `_KNOWN_PAIR_KEYS`
Liste exhaustive des clés valides dans `pair_state`. Toute clé inconnue est ignorée (protection contre la corruption partielle).

Clés essentielles :
| Clé | Type | Description |
|-----|------|-------------|
| `in_position` | bool | Position ouverte ou non |
| `entry_price` | float | Prix d'entrée |
| `quantity` | float | Quantité détenue |
| `sl_order_id` | str\|None | ID de l'ordre stop-loss exchange |
| `sl_exchange_placed` | bool | SL effectivement posé sur l'exchange |
| `sl_price` | float | Prix du stop-loss |
| `oos_blocked` | bool | Bloqué par les OOS gates WF |
| `consecutive_failures` | int | Compteur d'échecs `save_bot_state()` |
| `last_sl_hit_candle` | str\|None | Timestamp de la dernière bougie avec SL hit |
| `active_scenario` | str\|None | Scénario WF actif sélectionné |

## Thread-safety — RÈGLE ABSOLUE
- `_bot_state_lock` (RLock) doit être acquis pour **toute** lecture ou écriture de `bot_state`
- Ne jamais lire `bot_state["pairs"][pair]` sans le lock
- `save_bot_state()` est throttlé à 5s — utiliser `force=True` pour les saves critiques (post-trade)

## Comportement de `save_bot_state()`
- 3 échecs consécutifs → `emergency_halt = True` + alerte email P0
- Backup atomique : écriture dans `.tmp` puis `os.replace()` (atomique sur Windows)
- `bot_state.json.bak` : copie de sauvegarde conservée

## Réconciliation
- Au démarrage si `StateError` : `reconcile_positions_with_exchange()` appelle l'API pour reconstruire l'état réel
- Vérifie les ordres SL ouverts sur l'exchange pour repeupler `sl_order_id`

## À ne jamais faire
- Accéder à `bot_state` sans `_bot_state_lock`
- Modifier `_KNOWN_PAIR_KEYS` sans validation des migrations
- Logger le contenu brut de `bot_state` si il contient des clés sensibles
