import logging
import os
import sys
import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock

# C-01: forcer BOT_MODE=LIVE pour les tests (avant tout import de bot_config)
# Sans cela, le défaut DEMO bloquerait safe_market_buy/sell dans les tests d'ordre.
os.environ.setdefault('BOT_MODE', 'LIVE')

# Ensure code/src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))


@pytest.fixture(autouse=True)
def disable_error_notification_callback():
    """Neutralise le callback d'erreur de log_exceptions pendant les tests.

    Sans ce fixture, toute exception attrapée par @log_exceptions pendant
    pytest déclencherait un envoi d'email vers le compte Binance réel.
    """
    try:
        from bot_config import set_error_notification_callback
        set_error_notification_callback(None)
    except Exception:
        pass
    yield
    try:
        from bot_config import set_error_notification_callback
        set_error_notification_callback(None)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _block_all_emails(monkeypatch):
    """Empêche tout envoi d'email réel pendant les tests.

    Neutralise ``email_utils.send_email_alert`` et ``send_trading_alert_email``
    à la source : tous les modules qui les importent seront bloqués.
    P1-09: ajouter send_trading_alert_email pour couvrir les alertes OOS/SL/P1-04.
    """
    monkeypatch.setattr('email_utils.send_email_alert', lambda *a, **kw: False)
    monkeypatch.setattr('email_utils.send_trading_alert_email', lambda *a, **kw: False)


@pytest.fixture(autouse=True)
def cleanup_logger_handlers():
    yield
    # Cleanup all handlers for all loggers after each test
    for logger_name in logging.root.manager.loggerDict:
        logger = logging.getLogger(logger_name)
        for handler in getattr(logger, 'handlers', []):
            try:
                handler.flush()
                handler.close()
            except Exception:
                pass
        logger.handlers = []
    # Also cleanup root logger
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
    logging.getLogger().handlers = []


@pytest.fixture
def sample_config():
    """Instance Config de test avec valeurs de test (sans .env requis)."""
    class TestConfig:
        api_key = "test_api_key"
        secret_key = "test_secret_key"
        sender_email = "test@test.com"
        receiver_email = "recv@test.com"
        smtp_password = "test_pwd"
        api_timeout = 30
        smtp_server = "smtp.gmail.com"
        smtp_port = 587
        taker_fee = 0.0007
        maker_fee = 0.0002
        backtest_taker_fee = 0.0007   # P3-03: frais figés pour le backtest
        backtest_maker_fee = 0.0002   # P3-03: frais figés pour le backtest
        slippage_buy = 0.0001
        slippage_sell = 0.0001
        initial_wallet = 10000.0
        backtest_days = 1825
        max_workers = 2
        cache_dir = "cache"
        states_dir = "states"
        state_file = "bot_state.json"
        atr_period = 14
        atr_multiplier = 5.5
        atr_stop_multiplier = 3.0
        recv_window = 60000
        risk_per_trade = 0.05
        sizing_mode = "risk"
        partial_threshold_1 = 0.02
        partial_threshold_2 = 0.04
        partial_pct_1 = 0.50
        partial_pct_2 = 0.30
        trailing_activation_pct = 0.03
        target_volatility_pct = 0.02
        backtest_min_notional = 5.0
        oos_sharpe_min = 0.8
        oos_win_rate_min = 30.0
        oos_decay_min = 0.15
        schedule_interval_minutes = 2
        risk_free_rate = 0.04
        email_cooldown_seconds = 300
        stoch_rsi_buy_max = 0.8
        stoch_rsi_buy_min = 0.05
        stoch_rsi_sell_exit = 0.2
        adx_threshold = 25.0
        max_parallel_pairs = 5
        backtest_throttle_seconds = 3600.0
        project_name = "MULTI_ASSETS"
    return TestConfig()


@pytest.fixture
def sample_ohlcv_df():
    """DataFrame OHLCV de 1000 lignes avec données réalistes (random walk)."""
    np.random.seed(42)
    n = 1000
    dates = pd.date_range(start='2023-01-01', periods=n, freq='1h')

    # Random walk price starting at 100
    returns = np.random.normal(0.0002, 0.015, n)
    close = 100 * np.exp(np.cumsum(returns))

    # OHLC from close
    high = close * (1 + np.abs(np.random.normal(0, 0.005, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, n)))
    open_ = close * (1 + np.random.normal(0, 0.003, n))
    volume = np.random.uniform(1000, 50000, n)

    df = pd.DataFrame({
        'open': open_,
        'high': high,
        'low': low,
        'close': close,
        'volume': volume,
    }, index=dates)

    # Add indicators used by backtest
    from ta.volatility import AverageTrueRange
    from ta.momentum import RSIIndicator

    atr_indicator = AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14)
    df['atr'] = atr_indicator.average_true_range()

    # EMA columns
    df['ema1'] = df['close'].ewm(span=26, adjust=False).mean()
    df['ema2'] = df['close'].ewm(span=50, adjust=False).mean()

    # StochRSI
    rsi = RSIIndicator(close=df['close'], window=14).rsi()
    stoch_rsi = (rsi - rsi.rolling(14).min()) / (rsi.rolling(14).max() - rsi.rolling(14).min())
    df['stoch_rsi'] = stoch_rsi.fillna(0.5)

    df.dropna(inplace=True)
    return df


@pytest.fixture
def mock_binance_client():
    """Mock de BinanceFinalClient avec les méthodes utilisées.

    P2-06: get_exchange_info et get_symbol_info acceptent n'importe quel symbole
    au lieu de hardcoder BTCUSDC.
    """
    mock = MagicMock()
    mock.api_key = "mock_api_key"
    mock.api_secret = "mock_api_secret"
    mock._server_time_offset = -2000
    mock._sync_server_time = MagicMock()

    # get_account
    mock.get_account.return_value = {
        'balances': [
            {'asset': 'USDC', 'free': '10000.00', 'locked': '0.00'},
            {'asset': 'BTC', 'free': '0.5', 'locked': '0.0'},
            {'asset': 'ETH', 'free': '10.0', 'locked': '0.0'},
            {'asset': 'SOL', 'free': '50.0', 'locked': '0.0'},
        ]
    }

    # P2-06: filtre LOT_SIZE + MIN_NOTIONAL générique, utilisable pour tout symbole
    _default_filters = [
        {'filterType': 'LOT_SIZE', 'minQty': '0.00001', 'stepSize': '0.00001'},
        {'filterType': 'MIN_NOTIONAL', 'minNotional': '10.0'},
    ]

    def _make_symbol_info(symbol='BTCUSDC'):
        return {'symbol': symbol, 'filters': list(_default_filters)}

    # get_exchange_info — retourne les symboles demandés dynamiquement
    def _exchange_info(*args, **kwargs):
        # Si quelqu'un passe symbol=, on l'utilise ; sinon default multi-paires
        return {
            'symbols': [
                _make_symbol_info('BTCUSDC'),
                _make_symbol_info('ETHUSDC'),
                _make_symbol_info('SOLUSDC'),
            ]
        }
    mock.get_exchange_info.side_effect = _exchange_info

    # get_symbol_info — retourne les infos pour le symbole demandé
    def _symbol_info(symbol='BTCUSDC'):
        return _make_symbol_info(symbol)
    mock.get_symbol_info.side_effect = _symbol_info

    # get_server_time
    mock.get_server_time.return_value = {'serverTime': 1700000000000}

    # get_historical_klines (returns list of lists like Binance)
    import time
    now = int(time.time() * 1000)
    mock.get_historical_klines.return_value = [
        [now - i * 3600000, '100.0', '105.0', '95.0', '102.0', '1000.0',
         now - i * 3600000 + 3599999, '100000.0', 50, '500.0', '50000.0', '0']
        for i in range(200)
    ]

    return mock
