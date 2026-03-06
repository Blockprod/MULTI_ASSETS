# exchange_client.py — Contexte module

## Rôle
Client HTTP Binance Spot. Seul point d'entrée vers l'API Binance dans tout le projet. Aucun autre module ne doit importer `python-binance` directement.

## Classe principale : `BinanceFinalClient`

### Token Bucket (rate limiting)
- `_TokenBucket(rate=18.0, capacity=18.0)` : 18 requêtes/seconde max
- **Toutes** les méthodes API appellent `_rate_limiter.acquire()` avant exécution
- Ne jamais contourner le rate limiter, même pour des requêtes "légères"

### Synchronisation horloge
- `_server_time_offset` : calculé dynamiquement à l'init et toutes les 180s
- Formule : `offset = serverTime - (localBefore + latency/2) - 500ms`
- Clamp : `max(-10 000ms, min(+1 000ms, adjusted_offset))`
- Fallback si sync échoue : `-2000ms` (conservateur)
- Resync périodique : `schedule.every(30).minutes.do(_periodic_timestamp_resync)` dans `MULTI_SYMBOLS.py`

### Idempotence des ordres
- `origClientOrderId` (UUID) généré **avant** le premier try
- Sur retry (attempt > 0) : `get_order(origClientOrderId=...)` d'abord
- Si ordre `FILLED` ou `PARTIALLY_FILLED` → retourner sans re-soumettre
- **Ne jamais** réessayer un ordre sans vérifier son statut existant

### recvWindow
- Centralisé : `config.recv_window = 60 000ms`
- **Ne pas** passer `recvWindow` en dur dans les appels — toujours `config.recv_window`

## Contraintes absolues
- `TRAILING_STOP_MARKET` → **NotImplementedError** — n'existe pas sur Spot
- `STOP_LOSS_LIMIT` : seul type de stop-loss exchange supporté
- Timeout requête : 45s (`requests_params={'timeout': 45}`)
- `taker_fee=0.0007`, `maker_fee=0.0002` sont des constantes live — ne jamais les utiliser pour le backtest

## Méthodes critiques
| Méthode | Comportement |
|---------|-------------|
| `safe_market_buy(pair, qty)` | Idempotent, vérifie origClientOrderId sur retry |
| `safe_market_sell(pair, qty)` | Idempotent, fallback d'urgence si SL non posé |
| `place_stop_loss_limit(pair, qty, stop_price, limit_price)` | Pose le SL exchange immédiatement après BUY |
| `cancel_order(pair, order_id)` | Annule le SL existant avant SELL |
| `get_symbol_filters(pair)` | Récupère LOT_SIZE, MIN_NOTIONAL depuis l'exchange |
| `reconcile_balance()` | Sync soldes USDC et coins depuis l'API |

## Exceptions levées
- `RateLimitError` → attente back-off, retry automatique
- `InsufficientFundsError` → log + skip cycle
- `BalanceUnavailableError` → cycle sauté (API solde inaccessible)
- `OrderError` → log + escalade vers `error_handler`
