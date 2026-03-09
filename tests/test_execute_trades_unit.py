"""
test_execute_trades_unit.py — Tests unitaires pour execute_real_trades / _execute_real_trades_inner.

Couvre :
  - Emergency halt
  - Concurrence par paire (lock non-blocking)
  - Flow achat (baseline, risk, fixed_notional, volatility_parity, fallback)
  - Flow vente (SIGNAL 100%, PARTIAL-1, PARTIAL-2)
  - Stop-loss fixe et trailing stop
  - Anti-double-buy guard
  - OOS gate block
  - Dust detection
  - Paper trading
  - Position sizing edge (zero balance, min notional)
  - SL placement avec rollback + emergency halt double échec
"""

import os
import sys
import time
import threading
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

import MULTI_SYMBOLS as ms
from bot_config import Config


# ---------------------------------------------------------------------------
#  Fixtures
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Crée un Config minimal pour les tests."""
    cfg = Config()
    defaults = dict(
        api_key='k', secret_key='s',
        sender_email='a@b.c', receiver_email='d@e.f', smtp_password='p',
        taker_fee=0.0007, maker_fee=0.0002,
        backtest_taker_fee=0.0007, backtest_maker_fee=0.0002,
        slippage_buy=0.0001, slippage_sell=0.0001,
        initial_wallet=10000.0, atr_period=14,
        atr_multiplier=5.5, atr_stop_multiplier=3.0,
        risk_per_trade=0.05, sizing_mode='baseline',
        partial_threshold_1=0.02, partial_threshold_2=0.04,
        partial_pct_1=0.50, partial_pct_2=0.30,
        trailing_activation_pct=0.03, target_volatility_pct=0.02,
        cache_dir='cache', states_dir='states', state_file='bot_state.json',
        backtest_days=1095, max_workers=4,
        smtp_server='smtp.gmail.com', smtp_port=587,
        api_timeout=30,
        oos_sharpe_min=0.8, oos_win_rate_min=30.0, oos_decay_min=0.15,
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(cfg, k, v)
    return cfg


def _make_best_params(**overrides):
    """Paramètres de stratégie minimaux."""
    p = {
        'ema1_period': 26, 'ema2_period': 50,
        'scenario': 'StochRSI', 'timeframe': '1h',
        'sma_long': None, 'adx_period': None,
        'trix_length': None, 'trix_signal': None,
    }
    p.update(overrides)
    return p


def _exchange_info_mock(min_qty='0.001', step_size='0.001', min_notional='5.0',
                        max_qty='10000.0', symbol='TRXUSDC'):
    return {
        'symbols': [{
            'symbol': symbol,
            'filters': [
                {'filterType': 'LOT_SIZE', 'minQty': min_qty,
                 'stepSize': step_size, 'maxQty': max_qty},
                {'filterType': 'MIN_NOTIONAL', 'minNotional': min_notional},
            ],
        }]
    }


def _mock_df_row(close=100.0, atr=2.0, stoch_rsi=0.15, ema1=98.0, ema2=96.0):
    """Crée un SimpleNamespace qui imite une ligne de DataFrame."""
    import pandas as pd
    row = {'close': close, 'atr': atr, 'stoch_rsi': stoch_rsi,
           'ema1': ema1, 'ema2': ema2, 'open': close,
           'high': close * 1.01, 'low': close * 0.99,
           'rsi': 50, 'adx': 25}
    return pd.Series(row)


def _mock_df(n=100, close=100.0, atr=2.0):
    """Crée un DataFrame minimal avec n lignes."""
    import pandas as pd
    import numpy as np
    data = {
        'close': np.full(n, close),
        'open': np.full(n, close),
        'high': np.full(n, close * 1.01),
        'low': np.full(n, close * 0.99),
        'atr': np.full(n, atr),
        'stoch_rsi': np.full(n, 0.15),
        'ema_26': np.full(n, close * 0.98),
        'ema_50': np.full(n, close * 0.96),
        'rsi': np.full(n, 50.0),
    }
    return pd.DataFrame(data)


@pytest.fixture(autouse=True)
def _isolate_module_state(monkeypatch):
    """Isole le state global du module avant chaque test."""
    original_bot_state = ms.bot_state.copy()
    original_config = ms.config
    monkeypatch.setattr(ms, 'bot_state', {})
    monkeypatch.setattr(ms, '_save_failure_count', 0)
    monkeypatch.setattr(ms, '_last_save_time', 0.0)
    # Neutraliser les side-effects
    monkeypatch.setattr(ms, 'save_bot_state', lambda force=False: None)
    monkeypatch.setattr(ms, 'send_trading_alert_email', lambda **kw: None)
    monkeypatch.setattr(ms, 'log_trade', lambda **kw: None)
    # Neutraliser les fonctions d'affichage importées de display_ui
    for _fn in ('display_closure_panel', 'display_sell_signal_panel',
                'display_buy_signal_panel', 'display_account_balances_panel',
                'display_market_changes', 'display_results_for_pair',
                'display_backtest_table', 'display_trading_panel',
                'build_tracking_panel', 'display_execution_header',
                'display_bot_active_banner'):
        if hasattr(ms, _fn):
            monkeypatch.setattr(ms, _fn, lambda *a, **kw: None)
    yield


# ---------------------------------------------------------------------------
#  Tests: Emergency Halt
# ---------------------------------------------------------------------------

class TestEmergencyHalt:
    def test_emergency_halt_blocks_execution(self, monkeypatch):
        """Si emergency_halt est True, _execute_real_trades_inner retourne immédiatement."""
        monkeypatch.setattr(ms, 'bot_state', {
            'emergency_halt': True,
            'emergency_halt_reason': 'test halt',
        })
        # Ne doit pas lever d'exception ni appeler l'API
        result = ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert result is None

    def test_no_emergency_halt_proceeds(self, monkeypatch):
        """Sans emergency_halt, l'exécution continue (échoue sur client, prouvant qu'on ne bloque pas)."""
        monkeypatch.setattr(ms, 'bot_state', {})
        mock_client = MagicMock()
        mock_client.get_account.side_effect = Exception("client error - expected in test")
        monkeypatch.setattr(ms, 'client', mock_client)
        # Doit tenter d'appeler get_account (et lever l'exception capturée)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        mock_client.get_account.assert_called_once()


# ---------------------------------------------------------------------------
#  Tests: Concurrence / per-pair lock
# ---------------------------------------------------------------------------

class TestConcurrence:
    def test_concurrent_execution_skipped(self, monkeypatch):
        """Une exécution concurrente pour la même paire est ignorée."""
        lock = threading.Lock()
        lock.acquire()  # verrouiller manuellement
        monkeypatch.setattr(ms, '_pair_execution_locks', {'TRX/USDC': lock})

        calls = []
        monkeypatch.setattr(ms, '_execute_real_trades_inner', lambda *a, **kw: calls.append(1))

        ms.execute_real_trades('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(calls) == 0  # inner n'a pas été appelé
        lock.release()

    def test_different_pair_not_blocked(self, monkeypatch):
        """Des paires différentes ne se bloquent pas mutuellement."""
        lock = threading.Lock()
        lock.acquire()
        monkeypatch.setattr(ms, '_pair_execution_locks', {'TRX/USDC': lock})

        calls = []
        monkeypatch.setattr(ms, '_execute_real_trades_inner', lambda *a, **kw: calls.append(1))

        ms.execute_real_trades('BTCUSDC', '1h', _make_best_params(), 'BTC/USDC')
        assert len(calls) == 1
        lock.release()


# ---------------------------------------------------------------------------
#  Tests: Buy Flow — sizing modes
# ---------------------------------------------------------------------------

class TestBuyFlow:
    """Tests du flow d'achat complet."""

    def _setup_buy_env(self, monkeypatch, sizing_mode='baseline', usdc=1000.0,
                       buy_order_filled=True, price=100.0, atr=2.0):
        """Configure l'environnement pour un test d'achat."""
        cfg = _make_config(sizing_mode=sizing_mode)
        monkeypatch.setattr(ms, 'config', cfg)

        account = {
            'balances': [
                {'asset': 'USDC', 'free': str(usdc)},
                {'asset': 'TRX', 'free': '0.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': str(price)}
        mock_client.get_all_orders.return_value = []  # pas d'ordres remplis
        monkeypatch.setattr(ms, 'client', mock_client)

        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        df = _mock_df(close=price, atr=atr)
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)

        # Buy condition True
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                           lambda bp: lambda row, usdc_bal: (True, 'EMA_CROSS'))
        # Sell condition False
        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                           lambda bp: lambda *a: (False, None))

        monkeypatch.setattr(ms, 'get_usdc_from_all_sells_since_last_buy', lambda p: usdc)
        monkeypatch.setattr(ms, 'get_sniper_entry_price', lambda p, price: price)
        monkeypatch.setattr(ms, 'check_if_order_executed', lambda orders, side: False)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (False, False))
        monkeypatch.setattr(ms, 'can_execute_partial_safely', lambda **kw: True)

        buy_order = {
            'orderId': '123', 'status': 'FILLED' if buy_order_filled else 'NEW',
            'executedQty': '9.500', 'cummulativeQuoteQty': '950.0',
            'price': str(price),
        }
        buy_calls = []
        def track_buy(**kwargs):
            buy_calls.append(kwargs)
            return buy_order
        monkeypatch.setattr(ms, 'safe_market_buy', track_buy)

        sl_calls = []
        def track_sl(**kwargs):
            sl_calls.append(kwargs)
            return {'orderId': 'SL_1', 'status': 'NEW'}
        monkeypatch.setattr(ms, 'place_exchange_stop_loss_order', track_sl)
        monkeypatch.setattr(ms, 'safe_market_sell', lambda **kw: {'status': 'FILLED'})

        return buy_calls, sl_calls, mock_client

    def test_buy_baseline_sizing(self, monkeypatch):
        """Sizing baseline: 98% du capital (identique au backtest)."""
        buy_calls, sl_calls, _ = self._setup_buy_env(monkeypatch, sizing_mode='baseline',
                                                      usdc=1000.0, price=100.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC', 'baseline')
        assert len(buy_calls) == 1
        # min(usdc_for_buy=1000, usdc_balance=1000) * 0.98 = 980 USDC
        assert abs(buy_calls[0]['quoteOrderQty'] - 980.0) < 1.0

    def test_buy_risk_sizing(self, monkeypatch):
        """Sizing risk: utilise compute_position_size_by_risk."""
        buy_calls, _, _ = self._setup_buy_env(monkeypatch, sizing_mode='risk',
                                               usdc=10000.0, price=100.0, atr=2.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC', 'risk')
        assert len(buy_calls) == 1
        # risk_per_trade=5%, atr_stop_multiplier=3.0, atr=2.0
        # qty_risk = (10000 * 0.05) / (3.0 * 2.0) = 83.33 coins
        # quote = 83.33 * 100 = 8333 < 9500 (95% cap)
        assert buy_calls[0]['quoteOrderQty'] < 9500.0

    def test_buy_fixed_notional_sizing(self, monkeypatch):
        """Sizing fixed_notional: 10% du capital."""
        buy_calls, _, _ = self._setup_buy_env(monkeypatch, sizing_mode='fixed_notional',
                                               usdc=10000.0, price=100.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC', 'fixed_notional')
        assert len(buy_calls) == 1
        # 10% de 10000 = 1000 USDC max
        assert buy_calls[0]['quoteOrderQty'] <= 1000.1

    def test_buy_volatility_parity_sizing(self, monkeypatch):
        """Sizing volatility_parity: ATR-based."""
        buy_calls, _, _ = self._setup_buy_env(monkeypatch, sizing_mode='volatility_parity',
                                               usdc=10000.0, price=100.0, atr=2.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC', 'volatility_parity')
        assert len(buy_calls) == 1
        # qty_vol = (10000 * 0.02) / (2.0 * 100) = 1.0 coins → 100 USDC
        assert buy_calls[0]['quoteOrderQty'] <= 200.0

    def test_buy_unknown_sizing_fallback(self, monkeypatch):
        """Sizing inconnu → fallback baseline (98% du capital)."""
        buy_calls, _, _ = self._setup_buy_env(monkeypatch, sizing_mode='unknown_mode',
                                               usdc=1000.0, price=100.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC', 'unknown_mode')
        assert len(buy_calls) == 1
        # Fallback baseline: min(1000, 1000) * 0.98 = 980 USDC
        assert abs(buy_calls[0]['quoteOrderQty'] - 980.0) < 1.0

    def test_buy_sets_pair_state(self, monkeypatch):
        """Après achat, pair_state contient entry_price, atr_at_entry, etc."""
        buy_calls, sl_calls, _ = self._setup_buy_env(monkeypatch, usdc=1000.0, price=100.0, atr=2.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')

        ps = ms.bot_state.get('TRX/USDC', {})
        assert ps.get('entry_price') == 100.0
        assert ps.get('atr_at_entry') == 2.0
        assert ps.get('last_order_side') == 'BUY'
        assert ps.get('trailing_stop_activated') is False

    def test_buy_places_stop_loss(self, monkeypatch):
        """P0-01: Un SL est placé immédiatement après l'achat."""
        buy_calls, sl_calls, _ = self._setup_buy_env(monkeypatch, usdc=1000.0, price=100.0, atr=2.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')

        assert len(sl_calls) >= 1
        # SL price = 100 - (3.0 * 2.0) = 94.0
        assert abs(sl_calls[0]['stop_price'] - 94.0) < 0.1

    def test_buy_risk_atr_invalid_fallback(self, monkeypatch):
        """P0-SL-GUARD: ATR invalide → achat bloqué (pas de fallback)."""
        buy_calls, _, _ = self._setup_buy_env(monkeypatch, sizing_mode='risk',
                                               usdc=1000.0, price=100.0, atr=0.0)
        # Remplacer par un DF avec atr=NaN (invalide)
        df = _mock_df(close=100.0, atr=0.0)
        import numpy as np
        df['atr'] = np.nan  # ATR invalide
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC', 'risk')
        # P0-SL-GUARD: ATR invalide → achat refusé, pas d'appel buy
        assert len(buy_calls) == 0

    def test_buy_blocked_when_atr_none(self, monkeypatch):
        """P0-SL-GUARD: ATR=None → achat bloqué (stop-loss incalculable)."""
        buy_calls, sl_calls, _ = self._setup_buy_env(monkeypatch, usdc=1000.0, price=100.0, atr=2.0)
        # Forcer ATR=None dans le DataFrame
        df = _mock_df(close=100.0, atr=2.0)
        df['atr'] = None
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(buy_calls) == 0, "ATR=None doit bloquer l'achat"
        assert len(sl_calls) == 0, "Aucun SL ne doit être tenté"

    def test_buy_blocked_when_atr_zero(self, monkeypatch):
        """P0-SL-GUARD: ATR=0 → achat bloqué."""
        buy_calls, sl_calls, _ = self._setup_buy_env(monkeypatch, usdc=1000.0, price=100.0, atr=0.0)
        df = _mock_df(close=100.0, atr=0.0)
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(buy_calls) == 0, "ATR=0 doit bloquer l'achat"


# ---------------------------------------------------------------------------
#  Tests: Anti-double-buy & OOS gate
# ---------------------------------------------------------------------------

class TestBuyGuards:
    def test_anti_double_buy(self, monkeypatch):
        """Si le dernier ordre est un BUY FILLED, le cycle d'achat est ignoré."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '1000'},
                {'asset': 'TRX', 'free': '0.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': '100'}
        mock_client.get_all_orders.return_value = [
            {'status': 'FILLED', 'side': 'BUY', 'orderId': '1'}
        ]
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        df = _mock_df()
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                           lambda bp: lambda row, usdc_bal: (True, 'EMA_CROSS'))
        monkeypatch.setattr(ms, 'check_if_order_executed', lambda orders, side: side == 'BUY')
        monkeypatch.setattr(ms, 'get_usdc_from_all_sells_since_last_buy', lambda p: 1000.0)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (False, False))
        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                           lambda bp: lambda *a: (False, None))

        buy_calls = []
        monkeypatch.setattr(ms, 'safe_market_buy', lambda **kw: buy_calls.append(1))

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(buy_calls) == 0  # pas d'achat

    def test_oos_blocked(self, monkeypatch):
        """P0-03: OOS gate bloquée → aucun achat."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)
        monkeypatch.setattr(ms, 'bot_state', {
            'TRX/USDC': {'oos_blocked': True, 'oos_blocked_since': time.time()}
        })

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '1000'},
                {'asset': 'TRX', 'free': '0.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': '100'}
        mock_client.get_all_orders.return_value = []
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        df = _mock_df()
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                           lambda bp: lambda row, usdc_bal: (True, 'EMA_CROSS'))
        monkeypatch.setattr(ms, 'check_if_order_executed', lambda orders, side: False)
        monkeypatch.setattr(ms, 'get_usdc_from_all_sells_since_last_buy', lambda p: 1000.0)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (False, False))
        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                           lambda bp: lambda *a: (False, None))

        buy_calls = []
        monkeypatch.setattr(ms, 'safe_market_buy', lambda **kw: buy_calls.append(1))

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(buy_calls) == 0

    def test_zero_usdc_blocks_buy(self, monkeypatch):
        """Capital disponible 0 → pas d'achat."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '0.0'},
                {'asset': 'TRX', 'free': '0.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': '100'}
        mock_client.get_all_orders.return_value = []
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        df = _mock_df()
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                           lambda bp: lambda row, usdc_bal: (True, 'EMA_CROSS'))
        monkeypatch.setattr(ms, 'check_if_order_executed', lambda orders, side: False)
        monkeypatch.setattr(ms, 'get_usdc_from_all_sells_since_last_buy', lambda p: 0.0)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (False, False))
        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                           lambda bp: lambda *a: (False, None))

        buy_calls = []
        monkeypatch.setattr(ms, 'safe_market_buy', lambda **kw: buy_calls.append(1))

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(buy_calls) == 0


# ---------------------------------------------------------------------------
#  Tests: Stop-loss & Trailing
# ---------------------------------------------------------------------------

class TestStopLoss:
    def _setup_position(self, monkeypatch, current_price, entry_price=100.0, atr=2.0,
                        trailing_activated=False, max_price=None, trailing_stop=0.0):
        """Setup: position ouverte avec coin en wallet."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        monkeypatch.setattr(ms, 'bot_state', {
            'TRX/USDC': {
                'last_order_side': 'BUY',
                'entry_price': entry_price,
                'atr_at_entry': atr,
                'stop_loss_at_entry': entry_price - (cfg.atr_stop_multiplier * atr),
                'trailing_activation_price_at_entry': entry_price + (cfg.atr_multiplier * atr),
                'trailing_stop_activated': trailing_activated,
                'max_price': max_price or entry_price,
                'trailing_stop': trailing_stop,
                'partial_taken_1': False,
                'partial_taken_2': False,
            }
        })

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '0.0'},
                {'asset': 'TRX', 'free': '10.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': str(current_price)}
        mock_client.get_all_orders.return_value = [
            {'status': 'FILLED', 'side': 'BUY', 'orderId': '1',
             'executedQty': '10.0', 'price': str(entry_price),
             'cummulativeQuoteQty': str(entry_price * 10)}
        ]
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        df = _mock_df(close=current_price, atr=atr)
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'is_valid_stop_loss_order', lambda *a: True)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (False, False))
        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                           lambda bp: lambda *a: (False, None))
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                           lambda bp: lambda row, usdc_bal: (False, None))

        sell_calls = []
        def track_sell(**kwargs):
            sell_calls.append(kwargs)
            return {'status': 'FILLED', 'executedQty': kwargs.get('quantity', '10.0')}
        monkeypatch.setattr(ms, 'safe_market_sell', track_sell)

        return sell_calls

    def test_fixed_stop_loss_triggered(self, monkeypatch):
        """Prix <= entry - 3*ATR → vente stop-loss fixe."""
        # entry=100, ATR=2, SL = 100 - 3*2 = 94
        sell_calls = self._setup_position(monkeypatch, current_price=93.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) >= 1

    def test_price_above_stop_no_sell(self, monkeypatch):
        """Prix > SL → pas de vente stop-loss."""
        sell_calls = self._setup_position(monkeypatch, current_price=98.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) == 0

    def test_trailing_activation(self, monkeypatch):
        """Prix >= entry + 5*ATR → trailing_stop_activated = True."""
        # entry=100, ATR=2, activation = 100 + 5.5*2 = 111
        sell_calls = self._setup_position(monkeypatch, current_price=112.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')

        ps = ms.bot_state.get('TRX/USDC', {})
        assert ps.get('trailing_stop_activated') is True

    def test_trailing_stop_hit(self, monkeypatch):
        """Trailing activé et prix descend sous trailing_stop → vente."""
        # entry=100, ATR=2, trailing_distance = 5.5*2 = 11
        # max_price=115, trailing_stop = 115-11 = 104
        sell_calls = self._setup_position(
            monkeypatch, current_price=103.0, entry_price=100.0, atr=2.0,
            trailing_activated=True, max_price=115.0, trailing_stop=104.0
        )
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) >= 1

    def test_trailing_stop_ratchets_up(self, monkeypatch):
        """Le trailing ne peut que monter (protection des gains)."""
        sell_calls = self._setup_position(
            monkeypatch, current_price=120.0, entry_price=100.0, atr=2.0,
            trailing_activated=True, max_price=118.0, trailing_stop=107.0
        )
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')

        ps = ms.bot_state.get('TRX/USDC', {})
        # max_price devrait être mis à jour à 120
        # trailing_stop = 120 - 5.5*2 = 109 > 107 → ratchet up
        assert ps.get('trailing_stop', 0) >= 107.0


# ---------------------------------------------------------------------------
#  Tests: SL placement failure → rollback → emergency halt
# ---------------------------------------------------------------------------

class TestSLRollback:
    def test_sl_failure_triggers_rollback(self, monkeypatch):
        """3 échecs SL → market-sell de rollback."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '1000'},
                {'asset': 'TRX', 'free': '0.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': '100'}
        mock_client.get_all_orders.return_value = []
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        df = _mock_df(close=100.0, atr=2.0)
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                           lambda bp: lambda row, usdc_bal: (True, 'EMA_CROSS'))
        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                           lambda bp: lambda *a: (False, None))
        monkeypatch.setattr(ms, 'check_if_order_executed', lambda orders, side: False)
        monkeypatch.setattr(ms, 'get_usdc_from_all_sells_since_last_buy', lambda p: 1000.0)
        monkeypatch.setattr(ms, 'get_sniper_entry_price', lambda p, price: price)
        monkeypatch.setattr(ms, 'can_execute_partial_safely', lambda **kw: True)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (False, False))

        monkeypatch.setattr(ms, 'safe_market_buy', lambda **kw: {
            'orderId': '123', 'status': 'FILLED',
            'executedQty': '9.500', 'cummulativeQuoteQty': '950.0',
            'price': '100',
        })

        # SL échoue systématiquement
        monkeypatch.setattr(ms, 'place_exchange_stop_loss_order',
                           MagicMock(side_effect=Exception("SL fail")))

        sell_calls = []
        monkeypatch.setattr(ms, 'safe_market_sell', lambda **kw: (sell_calls.append(kw), {'status': 'FILLED'})[1])

        # Accélérer les retries
        monkeypatch.setattr(ms.time, 'sleep', lambda t: None)
        monkeypatch.setattr(ms.random, 'random', lambda: 0.0)

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')

        # Rollback (market-sell) doit être appelé
        assert len(sell_calls) >= 1
        ps = ms.bot_state.get('TRX/USDC', {})
        assert ps.get('last_order_side') == 'SELL'

    def test_double_failure_activates_emergency_halt(self, monkeypatch):
        """SL échoue 3x + rollback échoue → emergency_halt = True."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '1000'},
                {'asset': 'TRX', 'free': '0.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': '100'}
        mock_client.get_all_orders.return_value = []
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        df = _mock_df(close=100.0, atr=2.0)
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                           lambda bp: lambda row, usdc_bal: (True, 'EMA_CROSS'))
        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                           lambda bp: lambda *a: (False, None))
        monkeypatch.setattr(ms, 'check_if_order_executed', lambda orders, side: False)
        monkeypatch.setattr(ms, 'get_usdc_from_all_sells_since_last_buy', lambda p: 1000.0)
        monkeypatch.setattr(ms, 'get_sniper_entry_price', lambda p, price: price)
        monkeypatch.setattr(ms, 'can_execute_partial_safely', lambda **kw: True)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (False, False))

        monkeypatch.setattr(ms, 'safe_market_buy', lambda **kw: {
            'orderId': '123', 'status': 'FILLED',
            'executedQty': '9.500', 'cummulativeQuoteQty': '950.0',
            'price': '100',
        })

        # SL + rollback échouent
        monkeypatch.setattr(ms, 'place_exchange_stop_loss_order',
                           MagicMock(side_effect=Exception("SL fail")))
        monkeypatch.setattr(ms, 'safe_market_sell',
                           MagicMock(side_effect=Exception("Rollback fail")))
        monkeypatch.setattr(ms.time, 'sleep', lambda t: None)
        monkeypatch.setattr(ms.random, 'random', lambda: 0.0)

        # Neutraliser save_bot_state mais capturer les force=True
        save_calls = []
        monkeypatch.setattr(ms, 'save_bot_state', lambda force=False: save_calls.append(force))

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')

        assert ms.bot_state.get('emergency_halt') is True
        assert 'Double échec' in ms.bot_state.get('emergency_halt_reason', '')


# ---------------------------------------------------------------------------
#  Tests: Sell flow (signal + partial)
# ---------------------------------------------------------------------------

class TestSellFlow:
    def _setup_sell_env(self, monkeypatch, sell_reason='SIGNAL', coin_balance=10.0,
                        entry_price=100.0, current_price=105.0, partial_taken_1=False,
                        partial_taken_2=False):
        """Configure l'environnement pour une vente."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        monkeypatch.setattr(ms, 'bot_state', {
            'TRX/USDC': {
                'last_order_side': 'BUY',
                'entry_price': entry_price,
                'atr_at_entry': 2.0,
                'stop_loss_at_entry': entry_price - 6.0,
                'trailing_activation_price_at_entry': entry_price + 11.0,
                'trailing_stop_activated': False,
                'max_price': entry_price,
                'trailing_stop': 0,
                'partial_taken_1': partial_taken_1,
                'partial_taken_2': partial_taken_2,
                'initial_position_size': coin_balance,
                'partial_enabled': True,
            }
        })

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '0.0'},
                {'asset': 'TRX', 'free': str(coin_balance)},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': str(current_price)}
        mock_client.get_all_orders.return_value = [
            {'status': 'FILLED', 'side': 'BUY', 'orderId': '1',
             'executedQty': str(coin_balance), 'price': str(entry_price),
             'cummulativeQuoteQty': str(entry_price * coin_balance)}
        ]
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        df = _mock_df(close=current_price, atr=2.0)
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'is_valid_stop_loss_order', lambda *a: True)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (partial_taken_1, partial_taken_2))

        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                           lambda bp: lambda *a: (True, sell_reason))
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                           lambda bp: lambda row, usdc_bal: (False, None))
        monkeypatch.setattr(ms, 'can_execute_partial_safely', lambda **kw: True)

        sell_calls = []
        def track_sell(**kwargs):
            sell_calls.append(kwargs)
            return {'status': 'FILLED', 'executedQty': kwargs.get('quantity', str(coin_balance)),
                    'cummulativeQuoteQty': str(float(kwargs.get('quantity', coin_balance)) * current_price)}
        monkeypatch.setattr(ms, 'safe_market_sell', track_sell)

        return sell_calls

    def test_signal_sell_100_pct(self, monkeypatch):
        """SIGNAL vente : 100% de la position, state reset."""
        sell_calls = self._setup_sell_env(monkeypatch, sell_reason='SIGNAL',
                                          coin_balance=10.0, current_price=105.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) >= 1

        ps = ms.bot_state.get('TRX/USDC', {})
        # Après SIGNAL, l'état doit être reset
        assert ps.get('entry_price') is None or ps.get('last_order_side') == 'SELL'

    def test_partial_1_sell_50_pct(self, monkeypatch):
        """PARTIAL-1 : vente de 50% de la position."""
        sell_calls = self._setup_sell_env(monkeypatch, sell_reason='PARTIAL-1',
                                          coin_balance=10.0, current_price=105.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) >= 1
        # La quantité vendue doit être ~50%
        if sell_calls:
            qty_sold = float(sell_calls[0].get('quantity', 0))
            assert qty_sold < 10.0  # moins que le total

    def test_partial_2_sell_30_pct(self, monkeypatch):
        """PARTIAL-2 : vente de 30% du restant."""
        sell_calls = self._setup_sell_env(monkeypatch, sell_reason='PARTIAL-2',
                                          coin_balance=5.0, current_price=105.0,
                                          partial_taken_1=True)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) >= 1


# ---------------------------------------------------------------------------
#  Tests: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_missing_coin_in_balances_returns(self, monkeypatch):
        """Si la crypto n'a pas de balance, On retourne immédiatement."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '1000'},
                # PAS de TRX
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        # Ne doit pas crasher
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        # Pas d'appel API supplémentaire
        mock_client.get_all_orders.assert_not_called()

    def test_empty_dataframe_returns(self, monkeypatch):
        """DataFrame vide → cycle ignoré."""
        import pandas as pd
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '1000'},
                {'asset': 'TRX', 'free': '0.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': '100'}
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: pd.DataFrame())
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: pd.DataFrame())

        buy_calls = []
        monkeypatch.setattr(ms, 'safe_market_buy', lambda **kw: buy_calls.append(1))

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(buy_calls) == 0

    def test_symbol_not_in_exchange_info(self, monkeypatch):
        """Symbol introuvable dans exchange_info → return."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '1000'},
                {'asset': 'TRX', 'free': '0.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        monkeypatch.setattr(ms, 'client', mock_client)

        # Exchange info avec un autre symbol
        monkeypatch.setattr(ms, 'get_cached_exchange_info',
                           lambda c: _exchange_info_mock(symbol='BTCUSDC'))

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        mock_client.get_all_orders.assert_not_called()

    def test_insufficient_data_returns(self, monkeypatch):
        """DataFrame avec < 2 lignes → cycle ignoré."""
        import pandas as pd
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '1000'},
                {'asset': 'TRX', 'free': '0.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': '100'}
        mock_client.get_all_orders.return_value = []
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        # DataFrame avec 1 seule ligne
        df_tiny = pd.DataFrame({'close': [100], 'open': [100], 'high': [101],
                                'low': [99], 'atr': [2.0], 'stoch_rsi': [0.15],
                                'ema_26': [98], 'ema_50': [96], 'rsi': [50]})
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df_tiny)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df_tiny)

        buy_calls = []
        monkeypatch.setattr(ms, 'safe_market_buy', lambda **kw: buy_calls.append(1))

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(buy_calls) == 0


# ---------------------------------------------------------------------------
#  Tests: Partial sells (profit_pct-based) — _execute_partial_sells
# ---------------------------------------------------------------------------

class TestPartialSells:
    """Tests pour _execute_partial_sells() basé sur profit_pct (miroir backtest)."""

    def _setup_partial_env(self, monkeypatch, *, coin_free=10.0, entry_price=100.0,
                           current_price=105.0, atr=2.0,
                           partial_taken_1=False, partial_taken_2=False,
                           partial_enabled=True, min_notional='5.0'):
        """Configure l'environnement pour tester les ventes partielles."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        ps_data = {
            'last_order_side': 'BUY',
            'entry_price': entry_price,
            'atr_at_entry': atr,
            'stop_loss_at_entry': entry_price - (cfg.atr_stop_multiplier * atr),
            'trailing_activation_price_at_entry': entry_price + (cfg.atr_multiplier * atr),
            'trailing_stop_activated': False,
            'max_price': entry_price,
            'trailing_stop': 0,
            'partial_taken_1': partial_taken_1,
            'partial_taken_2': partial_taken_2,
            'initial_position_size': coin_free,
            'partial_enabled': partial_enabled,
        }
        monkeypatch.setattr(ms, 'bot_state', {'TRX/USDC': ps_data})

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '0.0'},
                {'asset': 'TRX', 'free': str(coin_free), 'locked': '0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': str(current_price)}
        mock_client.get_all_orders.return_value = [
            {'status': 'FILLED', 'side': 'BUY', 'orderId': '1',
             'executedQty': str(coin_free), 'price': str(entry_price),
             'cummulativeQuoteQty': str(entry_price * coin_free)}
        ]
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info',
                            lambda c: _exchange_info_mock(min_notional=min_notional))
        df = _mock_df(close=current_price, atr=atr)
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'is_valid_stop_loss_order', lambda *a: True)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (partial_taken_1, partial_taken_2))
        monkeypatch.setattr(ms, 'can_execute_partial_safely', lambda **kw: True)
        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                            lambda bp: lambda *a: (False, None))
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                            lambda bp: lambda row, usdc_bal: (False, None))

        sell_calls = []
        def track_sell(**kwargs):
            sell_calls.append(kwargs)
            return {'status': 'FILLED'}
        monkeypatch.setattr(ms, 'safe_market_sell', track_sell)

        return sell_calls

    def test_partial1_triggers_at_threshold(self, monkeypatch):
        """PARTIAL-1 se déclenche quand profit_pct >= 2% (config.partial_threshold_1)."""
        # entry=100, current=103 → profit_pct=3% >= 2%
        sell_calls = self._setup_partial_env(monkeypatch, entry_price=100.0,
                                             current_price=103.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) >= 1
        ps = ms.bot_state.get('TRX/USDC', {})
        assert ps.get('partial_taken_1') is True

    def test_partial1_does_not_trigger_below_threshold(self, monkeypatch):
        """PARTIAL-1 ne se déclenche PAS si profit_pct < 2%."""
        # entry=100, current=101 → profit_pct=1% < 2%
        sell_calls = self._setup_partial_env(monkeypatch, entry_price=100.0,
                                             current_price=101.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) == 0
        ps = ms.bot_state.get('TRX/USDC', {})
        assert ps.get('partial_taken_1') is False

    def test_partial2_triggers_at_threshold(self, monkeypatch):
        """PARTIAL-2 se déclenche quand profit_pct >= 4% et partial_taken_1=True."""
        # entry=100, current=105 → profit_pct=5% >= 4%
        sell_calls = self._setup_partial_env(monkeypatch, entry_price=100.0,
                                             current_price=105.0,
                                             partial_taken_1=True)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) >= 1
        ps = ms.bot_state.get('TRX/USDC', {})
        assert ps.get('partial_taken_2') is True

    def test_both_partials_trigger_same_cycle(self, monkeypatch):
        """Les deux partials se déclenchent sur le même cycle si profit >= 4%."""
        # entry=100, current=105 → profit_pct=5% >= 2% AND >= 4%
        sell_calls = self._setup_partial_env(monkeypatch, entry_price=100.0,
                                             current_price=105.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        # Both partials should fire
        assert len(sell_calls) >= 2
        ps = ms.bot_state.get('TRX/USDC', {})
        assert ps.get('partial_taken_1') is True
        assert ps.get('partial_taken_2') is True

    def test_partial_disabled_skips(self, monkeypatch):
        """partial_enabled=False → aucun partial même si profit suffit."""
        # entry=100, current=105 → profit_pct=5%
        sell_calls = self._setup_partial_env(monkeypatch, entry_price=100.0,
                                             current_price=105.0,
                                             partial_enabled=False)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) == 0

    def test_partial_already_taken_no_retry(self, monkeypatch):
        """Si partial_taken_1=True, pas de re-vente même si profit suffisant."""
        sell_calls = self._setup_partial_env(monkeypatch, entry_price=100.0,
                                             current_price=103.0,
                                             partial_taken_1=True)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        # No sells (partial_taken_2 not reached at 3%)
        assert len(sell_calls) == 0

    def test_partial_uses_config_pct(self, monkeypatch):
        """Vente partielle utilise config.partial_pct_1 (50%) au lieu de hardcoded."""
        # entry=100, current=103, coin_free=10 → expected qty ≈ 10 * 0.50 = 5.0
        sell_calls = self._setup_partial_env(monkeypatch, coin_free=10.0,
                                             entry_price=100.0,
                                             current_price=103.0)
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        assert len(sell_calls) >= 1
        # Verify quantity is ~50% = 5.0 (within step size rounding)
        qty_sold = float(sell_calls[0]['quantity'])
        assert 4.9 <= qty_sold <= 5.1, f"Expected ~5.0 (50%), got {qty_sold}"

    def test_partial_min_notional_blocks_and_flags(self, monkeypatch):
        """Notional insuffisant → partial bloqué mais flag marqué True."""
        # coin=0.01, price=2.04, entry=2.0 → profit=2% OK
        # notional = 0.01 * 0.5 * 2.04 = 0.0102 < min_notional=5.0
        sell_calls = self._setup_partial_env(monkeypatch, coin_free=0.01,
                                             entry_price=2.0,
                                             current_price=2.04,
                                             atr=0.1, min_notional='5.0')
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        ps = ms.bot_state.get('TRX/USDC', {})
        assert ps.get('partial_taken_1') is True  # flagged to avoid retry
        # No actual sell executed
        assert len(sell_calls) == 0


# ---------------------------------------------------------------------------
#  Tests: Dust detection
# ---------------------------------------------------------------------------

class TestDust:
    def test_dust_detected_and_sold(self, monkeypatch):
        """Résidu < min_qty mais > 1% → tentative de vente forcée."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        # coin_balance = 0.0005 < min_qty=0.001 mais > 0.001*0.01 = 0.00001
        account = {
            'balances': [
                {'asset': 'USDC', 'free': '1000'},
                {'asset': 'TRX', 'free': '0.0005'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': '100'}
        mock_client.get_all_orders.return_value = []
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info',
                           lambda c: _exchange_info_mock(min_qty='0.001', min_notional='0.01'))

        df = _mock_df()
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'generate_buy_condition_checker',
                           lambda bp: lambda row, usdc_bal: (True, 'EMA_CROSS'))
        monkeypatch.setattr(ms, 'check_if_order_executed', lambda orders, side: False)
        monkeypatch.setattr(ms, 'get_usdc_from_all_sells_since_last_buy', lambda p: 1000.0)
        monkeypatch.setattr(ms, 'get_sniper_entry_price', lambda p, price: price)
        monkeypatch.setattr(ms, 'check_partial_exits_from_history', lambda p, ep: (False, False))
        monkeypatch.setattr(ms, 'can_execute_partial_safely', lambda **kw: True)
        monkeypatch.setattr(ms, 'generate_sell_condition_checker',
                           lambda bp: lambda *a: (False, None))

        sell_calls = []
        def track_sell(**kw):
            sell_calls.append(kw)
            return {'status': 'FILLED'}
        monkeypatch.setattr(ms, 'safe_market_sell', track_sell)
        monkeypatch.setattr(ms, 'safe_market_buy', lambda **kw: {
            'orderId': '123', 'status': 'FILLED',
            'executedQty': '9.500', 'cummulativeQuoteQty': '950.0', 'price': '100',
        })
        monkeypatch.setattr(ms, 'place_exchange_stop_loss_order',
                           lambda **kw: {'orderId': 'SL_1', 'status': 'NEW'})

        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
        # Le dust sell doit avoir été tenté
        assert len(sell_calls) >= 1


# ---------------------------------------------------------------------------
#  Tests: General error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_get_account_exception_caught(self, monkeypatch):
        """Exception dans get_account → capturée, pas de crash."""
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)

        mock_client = MagicMock()
        mock_client.get_account.side_effect = Exception("API unavailable")
        monkeypatch.setattr(ms, 'client', mock_client)

        # Ne doit pas lever d'exception
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')

    def test_exception_in_trade_journal_does_not_abort(self, monkeypatch):
        """Exception dans log_trade → capturée, pas d'impact sur le trade."""
        # Ce test vérifie que log_trade est dans un try/except
        cfg = _make_config()
        monkeypatch.setattr(ms, 'config', cfg)
        monkeypatch.setattr(ms, 'log_trade', MagicMock(side_effect=Exception("journal error")))

        # Configurer un trade SL qui déclenche log_trade
        monkeypatch.setattr(ms, 'bot_state', {
            'TRX/USDC': {
                'last_order_side': 'BUY', 'entry_price': 100.0,
                'atr_at_entry': 2.0, 'stop_loss_at_entry': 94.0,
                'trailing_activation_price_at_entry': 111.0,
                'trailing_stop_activated': False, 'max_price': 100.0,
                'trailing_stop': 0,
            }
        })

        account = {
            'balances': [
                {'asset': 'USDC', 'free': '0.0'},
                {'asset': 'TRX', 'free': '10.0'},
            ]
        }
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_symbol_ticker.return_value = {'price': '93'}  # sous SL
        mock_client.get_all_orders.return_value = [
            {'status': 'FILLED', 'side': 'BUY', 'orderId': '1',
             'executedQty': '10.0', 'price': '100',
             'cummulativeQuoteQty': '1000'}
        ]
        monkeypatch.setattr(ms, 'client', mock_client)
        monkeypatch.setattr(ms, 'get_cached_exchange_info', lambda c: _exchange_info_mock())

        df = _mock_df(close=93.0)
        monkeypatch.setattr(ms, 'fetch_historical_data', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'universal_calculate_indicators', lambda *a, **kw: df)
        monkeypatch.setattr(ms, 'is_valid_stop_loss_order', lambda *a: True)
        monkeypatch.setattr(ms, 'safe_market_sell', lambda **kw: {
            'status': 'FILLED', 'executedQty': '10.0'})

        # Ne doit pas crash malgré l'erreur journal
        ms._execute_real_trades_inner('TRXUSDC', '1h', _make_best_params(), 'TRX/USDC')
