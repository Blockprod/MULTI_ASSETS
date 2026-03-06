# Contraintes de trading Binance — MULTI_ASSETS

## API Binance Spot
| Contrainte | Valeur | Source code |
|-----------|--------|------------|
| Rate limit | 1200 req/min | `_TokenBucket(rate=18.0)` dans exchange_client.py |
| recvWindow | 60 000 ms | `config.recv_window = 60000` |
| Timeout requête | 45s | `kwargs['requests_params'] = {'timeout': 45}` |
| Resync horloge | toutes les 30 min | `schedule.every(30).minutes.do(_periodic_timestamp_resync)` |
| Offset timestamp | calculé dynamiquement | `real_offset - 500ms safety margin`, clampé [-10s, +1s] |

## Filtres de paire (LOT_SIZE, MIN_NOTIONAL)
- **LOT_SIZE** : chaque `quantity` doit être un multiple entier de `stepSize`
  - La quantité est arrondie par `(qty // stepSize) * stepSize` via `Decimal`
  - Utilisé dans `reconcile_positions_with_exchange()` et `_execute_buy()`
- **MIN_NOTIONAL** : valeur minimale d'un ordre en USDC
  - Backtest : `config.backtest_min_notional = 5.0 USDC` (simulé)
  - Live : vérifié via `get_symbol_filters()` → filtre `MIN_NOTIONAL`
- **MIN_QTY** : quantité minimale (souvent 0.001 pour les altcoins majeurs)
  - Seuil de réconciliation : `coin_balance >= 0.001` (dust < 0.001)

## Types d'ordres disponibles sur Spot
| Type | Disponible | Note |
|------|-----------|------|
| MARKET | ✅ | `safe_market_buy/sell` |
| STOP_LOSS_LIMIT | ✅ | Stop-loss exchange (C-02) |
| TRAILING_STOP_MARKET | ❌ | **Futures uniquement** — NotImplementedError |

## Fees
| Type | Valeur config | Utilisation |
|------|-------------|-------------|
| `taker_fee` | 0.0007 (0.07%) | frais live dans les ordres MARKET |
| `maker_fee` | 0.0002 (0.02%) | frais live dans les ordres LIMIT |
| `backtest_taker_fee` | 0.0007 | **FIGÉ** — jamais modifié par live |
| `backtest_maker_fee` | 0.0002 | **FIGÉ** — jamais modifié par live |
| `slippage_buy` | 0.0001 (0.01%) | slippage simulé en backtest |
| `slippage_sell` | 0.0001 (0.01%) | slippage simulé en backtest |

## Idempotence des ordres
- Chaque ordre a un `origClientOrderId` (UUID) généré avant le premier try
- Avant chaque retry (attempt > 0) : appel `get_order(origClientOrderId=...)`
- Si `FILLED` ou `PARTIALLY_FILLED` → retourne l'ordre existant sans re-soumettre
- Protection contre le double achat/vente en cas de timeout réseau

## Contraintes de sizing
- **Risk-based** (défaut): `qty = (equity × risk_pct) / (atr_stop_multiplier × ATR)`
  - `risk_pct = 5.5%` (`config.risk_per_trade`), `atr_stop_multiplier = 3.0` (`config.atr_stop_multiplier`)
- **Fixed notional**: `qty = notional_usdc / entry_price` (défaut: 10% equity)
- **Volatility parity**: `qty` ajustée pour cibler `target_volatility_pct = 2%` annualisé
- Retourne 0.0 si `ATR ≤ 0`, `entry_price ≤ 0`, or `equity ≤ 0` → lève `SizingError`

## Gestion du capital
- `initial_wallet = 10 000 USDC` (capital de référence pour daily_loss_limit)
- `daily_loss_limit_pct = 5%` → seuil = 500 USDC de perte journalière
- `partial_threshold_1 = 2%` → sortie partielle de 50% à +2%
- `partial_threshold_2 = 4%` → sortie partielle de 30% à +4%
- `trailing_activation_pct = 3%` → activation du trailing manuel
- `breakeven_trigger_pct = 2%` → stop-loss relevé au niveau du prix d'entrée à +2%
- `stop_loss_cooldown_candles = 12` → 12 heures de cooldown après un stop ou breakeven

## Synchronisation horloge (timestamp -1021)
- Offset calculé précisément : `offset = serverTime - (localBefore + latency/2) - 500ms`
- Clamp : `max(-10 000ms, min(+1 000ms, adjusted_offset))`
- Resync périodique : toutes les 180s dans `BinanceFinalClient._sync_interval`
- Fallback conservateur si la sync échoue : `_server_time_offset = -2000ms`
- `recvWindow = 60 000ms` absorbe les dérives résiduelles

## Hiérarchie des exceptions exchange
```
TradingBotError
├── ExchangeError
│   ├── RateLimitError          — back off & retry
│   ├── InsufficientFundsError  — balance insuffisante
│   ├── BalanceUnavailableError — API solde inaccessible → cycle sauté
│   └── OrderError              — échec placement/annulation ordre
├── StateError                  — corruption HMAC ou fichier état
└── CapitalProtectionError      — daily loss limit / kill-switch
```
