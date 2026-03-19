# Contraintes Binance API — MULTI_ASSETS

> Complémentaire à `knowledge/trading_constraints.md` qui couvre la logique
> de trading (sizing, capital, fees, idempotence).  
> Ce fichier documente l'API Binance elle-même : authentification, endpoints,
> codes d'erreur et différences Spot/Futures.

---

## Authentification des requêtes

| Paramètre | Valeur | Note |
|-----------|--------|------|
| Header `X-MBX-APIKEY` | `config.api_key` | Toutes les requêtes signées |
| Signature param `signature` | HMAC-SHA256 | Clé = `config.secret_key` |
| Timestamp | `int(time.time() * 1000) + _server_time_offset` | Offset calculé via `/api/v3/time` |
| Encoding | SHA256(secret_key, queryString + body) → hex | Appliqué côté client `python-binance` |

### Permissions API key requises
- ✅ **Enable Reading** — pour klines, account info, order status
- ✅ **Enable Spot & Margin Trading** — pour CREATE/CANCEL orders
- ❌ **Enable Futures** — inutile (bot Spot uniquement)
- ❌ **Enable Withdrawals** — JAMAIS activer (risque capital)

---

## Endpoints utilisés dans exchange_client.py

| Opération | Endpoint | Méthode | Poids |
|-----------|----------|---------|-------|
| Klines OHLCV | `/api/v3/klines` | GET | 2 |
| Info exchange (filtres) | `/api/v3/exchangeInfo` | GET | 20 |
| Temps serveur | `/api/v3/time` | GET | 1 |
| Solde du compte | `/api/v3/account` | GET (signed) | 20 |
| Placer un ordre | `/api/v3/order` | POST (signed) | 1+ |
| Consulter un ordre | `/api/v3/order` | GET (signed) | 4 |
| Annuler un ordre | `/api/v3/order` | DELETE (signed) | 1 |
| Annuler tous les ordres | `/api/v3/openOrders` | DELETE (signed) | 1 |
| Prix ticker | `/api/v3/ticker/price` | GET | 2 |

---

## Codes d'erreur HTTP et actions dans ce projet

| Code HTTP | Code Binance | Signification | Action dans exchange_client.py |
|-----------|-------------|----------------|-------------------------------|
| 429 | — | Rate limit dépassé | Back-off exponentiel, log WARNING |
| 418 | — | IP bannie | Log CRITICAL, email alerte, arrêt temporaire |
| 400 | -1021 | Timestamp hors fenêtre | `_sync_time_offset()` + retry |
| 400 | -1100 | Paramètre invalide | Log ERROR, pas de retry |
| 400 | -2010 | Fonds insuffisants | `BalanceUnavailableError`, skip cycle |
| 400 | -2011 | Annulation d'un ordre inconnu | Ignoré (ordre déjà rempli ou annulé) |
| 400 | -2013 | Ordre inexistant | Traité comme rempli (idempotence) |
| 503 | — | Service Binance indisponible | Retry avec back-off, log WARNING |

---

## Binance Spot vs Futures — Différences clés

| Feature | Spot | Futures | Impact sur ce projet |
|---------|------|---------|---------------------|
| Leverage | ❌ Non | ✅ Oui | Taille position = equity × risk_pct seulement |
| `TRAILING_STOP_MARKET` | ❌ Non | ✅ Oui | **`NotImplementedError`** si appelé ici |
| `STOP_LOSS_LIMIT` | ✅ Oui | ✅ Oui | Stop-loss exchange natif (ADR-003) |
| Short selling | ❌ Non | ✅ Oui | Bot long-only uniquement |
| Mark price / funding | ❌ Non | ✅ Oui | Pas de funding rate à absorber |
| Endpoint base | `api.binance.com` | `fapi.binance.com` | Client Spot exclusivement |
| `ISOLATED_MARGIN` | Optionnel | ✅ Oui | Bot account type = SPOT |

---

## Format de réponse ordre — champs critiques

```python
{
    "orderId": int,              # ID Binance de l'ordre
    "clientOrderId": str,        # = origClientOrderId si fourni (idempotence)
    "status": str,               # NEW / PARTIALLY_FILLED / FILLED / CANCELED / REJECTED
    "executedQty": str,          # Quantité exécutée (str → Decimal)
    "cummulativeQuoteQty": str,  # Valeur totale exécutée en USDC
    "price": str,                # Prix limite (0.0 pour MARKET)
    "type": str,                 # MARKET / STOP_LOSS_LIMIT
    "side": str,                 # BUY / SELL
    "fills": [                   # Seulement pour MARKET via /api/v3/order POST
        {"price": str, "qty": str, "commission": str, "commissionAsset": str}
    ]
}
```

### Interprétation du statut dans safe_market_buy/sell
- `FILLED` → succès, extraire prix moyen depuis `fills` ou `cummulativeQuoteQty / executedQty`
- `PARTIALLY_FILLED` → traité comme succès (quantité partielle acceptée)
- `CANCELED` / `REJECTED` → `OrderError` levée
- `NEW` → ordre en attente (impossible pour MARKET — lever timeout si pendant > 30s)

---

## Contraintes sur le type STOP_LOSS_LIMIT

```python
# Paramètres requis (Binance Spot)
{
    "symbol": "BTCUSDC",
    "side": "SELL",
    "type": "STOP_LOSS_LIMIT",
    "timeInForce": "GTC",        # Good Till Canceled
    "quantity": str,             # Arrondi stepSize
    "price": str,                # Prix limite (légèrement sous stopPrice)
    "stopPrice": str,            # Prix déclencheur
    "newClientOrderId": str,     # UUID pour idempotence
}
# stopPrice > price pour éviter le rejet (stopPrice = SL trigger, price = SL limit -0.1%)
```

---

## Compte Binance — Type et structure

- **Account type** : `SPOT` uniquement dans ce projet
- **Quote currency** : `USDC` pour toutes les paires (jamais `USDT`)
- **Solde disponible** : `asset['free']` dans la réponse `/api/v3/account`
- **Solde locked** : `asset['locked']` — inclut les ordres SL ouverts
- **Seuil de dust** : `coin_balance < 0.001` → ignoré lors de la réconciliation

---

## Disponibilité des WebSockets Binance Spot

| Canal | Disponible | Pourquoi non utilisé |
|-------|-----------|---------------------|
| Kline stream | ✅ | Polling REST suffisant pour cycle horaire |
| User data stream | ✅ | Complexité + point de failure supplémentaire |
| Order book | ✅ | Inutile (bot trend-following, pas market making) |

**Choix architectural** : REST polling synchrone toutes les heures, pas de WebSocket.  
Voir `ADR-004` et `ADR-006` dans `architecture/decisions.md`.
