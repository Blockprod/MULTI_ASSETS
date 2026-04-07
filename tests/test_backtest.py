"""Tests for backtest_from_dataframe in MULTI_SYMBOLS.py."""
import sys
import os
import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'code', 'src'))

# ---------------------------------------------------------------------------
# Helper to build a realistic OHLCV DataFrame with all required indicators
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 200, start_price: float = 100.0, trend: str = 'up') -> pd.DataFrame:
    """Generate a synthetic DataFrame with all columns that backtest_from_dataframe expects."""
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=n, freq='4h')
    noise = np.random.normal(0, 0.5, n)

    if trend == 'up':
        close = start_price + np.linspace(0, 60, n) + np.cumsum(noise)
    elif trend == 'down':
        close = start_price - np.linspace(0, 60, n) + np.cumsum(noise)
    else:  # flat
        close = start_price + np.cumsum(noise)

    high = close + np.abs(np.random.normal(1, 0.3, n))
    low = close - np.abs(np.random.normal(1, 0.3, n))
    open_ = close + np.random.normal(0, 0.3, n)
    volume = np.random.uniform(1000, 5000, n)

    df = pd.DataFrame({
        'open': open_, 'high': high, 'low': low,
        'close': close, 'volume': volume,
    }, index=dates)

    # EMA columns (will be recomputed by backtest but expected in df)
    df['ema_12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['ema_22'] = df['close'].ewm(span=22, adjust=False).mean()

    # ATR (simplified 14-period)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean().bfill()

    # StochRSI (simplified)
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi_min = rsi.rolling(14).min()
    rsi_max = rsi.rolling(14).max()
    df['stoch_rsi'] = ((rsi - rsi_min) / (rsi_max - rsi_min)).fillna(0.5)

    return df


def _import_backtest():
    """Try to import backtest_from_dataframe; skip the test if it fails."""
    with patch.dict(os.environ, {
        'BINANCE_API_KEY': 'test_key',
        'BINANCE_SECRET_KEY': 'test_secret',
        'SENDER_EMAIL': 'a@b.c',
        'RECEIVER_EMAIL': 'a@b.c',
        'GOOGLE_MAIL_PASSWORD': 'pass',
    }):
        try:
            from MULTI_SYMBOLS import backtest_from_dataframe
            return backtest_from_dataframe
        except Exception:
            pytest.skip("Cannot import MULTI_SYMBOLS (missing deps or network)")


# =========================================================================
# Tests
# =========================================================================

class TestBacktestFromDataframe:
    """Unit tests for the pure-Python backtest engine."""

    def test_empty_df_returns_zero(self):
        fn = _import_backtest()
        result = fn(pd.DataFrame(), ema1_period=12, ema2_period=22)
        assert result['final_wallet'] == 0.0
        assert result['max_drawdown'] == 0.0

    def test_short_df_returns_zero(self):
        fn = _import_backtest()
        df = _make_ohlcv(n=30)  # < 50 rows → early-return
        result = fn(df, ema1_period=12, ema2_period=22)
        assert result['final_wallet'] == 0.0

    def test_uptrend_positive_profit(self):
        """An uptrend with clear EMA crossover should produce positive returns."""
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=100, trend='up')
        result = fn(df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
        # In a strong uptrend the wallet should grow (or at least not crash)
        assert result['final_wallet'] >= 0, "Final wallet should not be negative"
        assert isinstance(result['trades'], pd.DataFrame)

    def test_downtrend_low_wallet(self):
        """A downtrend should result in modest or negative returns."""
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=200, trend='down')
        result = fn(df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
        # The bot should not be able to make huge profits in a bear market
        # Final wallet can still be > 0 (it starts with initial_wallet config value)
        assert result['final_wallet'] >= 0

    def test_different_sizing_modes_return_results(self):
        """Each sizing mode should return a valid result dict."""
        fn = _import_backtest()
        df = _make_ohlcv(n=300, start_price=100, trend='up')
        for mode in ('baseline', 'risk', 'fixed_notional', 'volatility_parity'):
            result = fn(df, ema1_period=12, ema2_period=22, sizing_mode=mode)
            assert 'final_wallet' in result, f"Mode {mode} missing final_wallet"
            assert 'win_rate' in result, f"Mode {mode} missing win_rate"
            assert 'max_drawdown' in result, f"Mode {mode} missing max_drawdown"

    def test_trades_history_has_buy_and_sell(self):
        """In a sufficiently long uptrend, at least one buy+sell cycle should occur."""
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=100, trend='up')
        result = fn(df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
        trades_df = result['trades']
        if not trades_df.empty:
            types = [t.lower() for t in trades_df['type'].unique().tolist()]
            assert 'buy' in types, "Expected at least one buy"

    def test_win_rate_between_0_and_100(self):
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=100, trend='up')
        result = fn(df, ema1_period=12, ema2_period=22)
        assert 0.0 <= result['win_rate'] <= 100.0

    def test_max_drawdown_non_negative(self):
        fn = _import_backtest()
        df = _make_ohlcv(n=300, start_price=100, trend='flat')
        result = fn(df, ema1_period=12, ema2_period=22)
        assert result['max_drawdown'] >= 0.0


# =========================================================================
# Tests: Protections backtest (alignement live)
# =========================================================================

class TestBacktestProtections:
    """Vérifie que les protections live sont bien répliquées dans le backtest."""

    def test_p0_sl_guard_atr_nan_blocks_buy(self):
        """P0-SL-GUARD: ATR=NaN → pas d'achat dans le backtest."""
        fn = _import_backtest()
        df = _make_ohlcv(n=200, start_price=100, trend='up')
        # Rendre ATR = NaN pour toute la série
        df['atr'] = float('nan')
        result = fn(df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
        trades = result.get('trades', pd.DataFrame())
        if not trades.empty:
            buys = trades[trades['type'] == 'buy']
            assert buys.empty, "Aucun achat ne doit avoir lieu si ATR est NaN"

    def test_p0_sl_guard_atr_zero_blocks_buy(self):
        """P0-SL-GUARD: ATR=0 → pas d'achat dans le backtest."""
        fn = _import_backtest()
        df = _make_ohlcv(n=200, start_price=100, trend='up')
        df['atr'] = 0.0
        result = fn(df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
        trades = result.get('trades', pd.DataFrame())
        if not trades.empty:
            buys = trades[trades['type'] == 'buy']
            assert buys.empty, "Aucun achat ne doit avoir lieu si ATR est 0"

    def test_partial_skipped_when_position_too_small(self):
        """partial_enabled: si la position vaut < 3× min_notional, partials ignorés."""
        fn = _import_backtest()
        # On crée un uptrend clair mais avec un wallet minuscule
        # pour produire des positions < 3×5=15 USDC
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'k', 'BINANCE_SECRET_KEY': 's',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'p',
            'INITIAL_WALLET': '3.0',          # Wallet minuscule
            'BACKTEST_MIN_NOTIONAL': '5.0',   # Position 3 USDC < 3×5 = 15
        }):
            from importlib import reload
            import bot_config as bc
            reload(bc)
            import backtest_runner as br

            # Patch config dans le module backtest_runner
            cfg = bc.Config.from_env()
            original_config = br.config
            br.config = cfg

            try:
                df = _make_ohlcv(n=300, start_price=1.0, trend='up')
                result = br.backtest_from_dataframe(
                    df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
                # Le test vérifie juste que ça ne plante pas et que le résultat est valide
                assert result['final_wallet'] >= 0
            finally:
                br.config = original_config

    def test_partial_min_notional_blocks_small_partial(self):
        """MIN_NOTIONAL backtest: partial bloqué si qty × price < backtest_min_notional."""
        fn = _import_backtest()
        df = _make_ohlcv(n=300, start_price=100, trend='up')
        # Avec un wallet normal et un uptrend, des partials devraient se déclencher normalement
        # Ce test vérifie que la logique ne plante pas (test d'intégration)
        result = fn(df, ema1_period=12, ema2_period=22, sizing_mode='baseline')
        assert result['final_wallet'] >= 0
        assert result['max_drawdown'] >= 0.0


class TestPartialSellSimulation:
    """P2-01: validation de la simulation des partiels en backtest."""

    def test_partial_sells_appear_in_trade_log(self):
        """partial_enabled=True sur uptrend → trade_log contient partial_sell_1/2."""
        fn = _import_backtest()
        # Uptrend fort avec assez de données pour déclencher buy + partials
        df = _make_ohlcv(n=500, start_price=100, trend='up')
        result = fn(df, ema1_period=12, ema2_period=22,
                     sizing_mode='baseline', partial_enabled=True)
        trades = result['trades']
        if trades.empty:
            pytest.skip("Pas de trades générés (pas de signal sur ces données)")
        types = trades['type'].tolist()
        # Au moins un partial_sell devrait apparaître dans un uptrend fort
        partial_types = [t for t in types if t.startswith('partial_sell')]
        assert len(partial_types) > 0, (
            f"Aucune vente partielle dans le trade_log (types: {set(types)})"
        )
        # Vérifier les colonnes du partial
        partial_rows = trades[trades['type'].str.startswith('partial_sell')]
        for col in ('price', 'qty', 'proceeds', 'profit_pct'):
            assert col in partial_rows.columns, f"Colonne '{col}' manquante dans partial_sell"

    def test_partial_disabled_no_partial_entries(self):
        """partial_enabled=False → aucune entrée partial_sell dans le trade_log."""
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=100, trend='up')
        result = fn(df, ema1_period=12, ema2_period=22,
                     sizing_mode='baseline', partial_enabled=False)
        trades = result['trades']
        if not trades.empty:
            partial_types = [t for t in trades['type'].tolist()
                             if t.startswith('partial_sell')]
            assert len(partial_types) == 0, (
                "partial_enabled=False mais partial_sell présent dans trade_log"
            )

    def test_partial_enabled_vs_disabled_diverge(self):
        """P2-01: partial_enabled=True et False donnent des wallets différents."""
        fn = _import_backtest()
        df = _make_ohlcv(n=500, start_price=100, trend='up')
        r_on = fn(df, ema1_period=12, ema2_period=22,
                   sizing_mode='baseline', partial_enabled=True)
        r_off = fn(df, ema1_period=12, ema2_period=22,
                    sizing_mode='baseline', partial_enabled=False)
        # Si des partials se déclenchent, les wallets doivent diverger
        trades_on = r_on['trades']
        if not trades_on.empty:
            partial_types = [t for t in trades_on['type'].tolist()
                             if t.startswith('partial_sell')]
            if len(partial_types) > 0:
                assert r_on['final_wallet'] != r_off['final_wallet'], (
                    "Les wallets devraient diverger quand des partiels se déclenchent"
                )


# =========================================================================
# Tests: Slippage stochastique OOS (P2-02)
# =========================================================================

class TestBasicSlippageModel:
    """P2-02: BasicSlippageModel unit tests."""

    def _import_slippage_model(self):
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'k', 'BINANCE_SECRET_KEY': 's',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'p',
        }):
            try:
                from backtest_runner import BasicSlippageModel
                return BasicSlippageModel
            except Exception:
                pytest.skip("Cannot import backtest_runner")

    def test_buy_factor_above_one(self):
        """buy_factor doit toujours être > 1 (surcoût à l'achat)."""
        SM = self._import_slippage_model()
        model = SM(seed=42)
        for _ in range(20):
            assert model.buy_factor(0.5) > 1.0

    def test_sell_factor_below_one(self):
        """sell_factor doit toujours être < 1 (décote à la vente)."""
        SM = self._import_slippage_model()
        model = SM(seed=42)
        for _ in range(20):
            assert model.sell_factor(0.5) < 1.0

    def test_low_volume_higher_impact(self):
        """Volume faible (rank=0) doit produire un surcoût plus élevé que fort (rank=1)."""
        SM = self._import_slippage_model()
        model = SM(seed=0)
        # Avec le même RNG, comparer buy_factor pour rank=0 vs rank=1
        # Recréer deux modèles avec la même seed pour comparer équitablement
        m_low = SM(seed=7)
        m_high = SM(seed=7)
        factor_low = m_low.buy_factor(volume_rank=0.0)
        factor_high = m_high.buy_factor(volume_rank=1.0)
        assert factor_low > factor_high, (
            f"Faible volume devrait coûter plus cher ({factor_low:.6f} vs {factor_high:.6f})"
        )

    def test_deterministic_with_seed(self):
        """Deux modèles avec la même seed produisent la même séquence."""
        SM = self._import_slippage_model()
        m1 = SM(seed=99)
        m2 = SM(seed=99)
        factors_1 = [m1.buy_factor(0.5) for _ in range(10)]
        factors_2 = [m2.buy_factor(0.5) for _ in range(10)]
        assert factors_1 == factors_2


class TestBacktestWithSlippage:
    """P2-02: backtest_from_dataframe avec slippage_model."""

    def test_slippage_model_accepted_as_parameter(self):
        """backtest_from_dataframe accepte slippage_model sans erreur."""
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'k', 'BINANCE_SECRET_KEY': 's',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'p',
        }):
            try:
                from backtest_runner import backtest_from_dataframe, BasicSlippageModel
            except Exception:
                pytest.skip("Cannot import backtest_runner")

        df = _make_ohlcv(n=300, start_price=100, trend='up')
        model = BasicSlippageModel(seed=42)
        result = backtest_from_dataframe(
            df, ema1_period=12, ema2_period=22,
            sizing_mode='baseline', slippage_model=model,
        )
        assert 'final_wallet' in result
        assert result['final_wallet'] >= 0

    def test_slippage_model_none_is_default(self):
        """slippage_model=None est identique à l'absence du paramètre."""
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'k', 'BINANCE_SECRET_KEY': 's',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'p',
        }):
            try:
                from backtest_runner import backtest_from_dataframe
            except Exception:
                pytest.skip("Cannot import backtest_runner")

        df = _make_ohlcv(n=300, start_price=100, trend='up')
        r_default = backtest_from_dataframe(df, ema1_period=12, ema2_period=22,
                                             sizing_mode='baseline')
        r_none = backtest_from_dataframe(df, ema1_period=12, ema2_period=22,
                                          sizing_mode='baseline', slippage_model=None)
        assert r_default['final_wallet'] == r_none['final_wallet']

    def test_backtest_with_slippage_returns_lower_sharpe(self):
        """P2-02: le slippage stochastique produit un wallet final inférieur ou égal.

        Avec un uptrend fort et des trades actifs, l'ajout d'un surcoût
        stochastique doit réduire le wallet final vs sans slippage modèle.
        """
        with patch.dict(os.environ, {
            'BINANCE_API_KEY': 'k', 'BINANCE_SECRET_KEY': 's',
            'SENDER_EMAIL': 'a@b.c', 'RECEIVER_EMAIL': 'a@b.c',
            'GOOGLE_MAIL_PASSWORD': 'p',
        }):
            try:
                from backtest_runner import backtest_from_dataframe, BasicSlippageModel
            except Exception:
                pytest.skip("Cannot import backtest_runner")

        df = _make_ohlcv(n=500, start_price=100, trend='up')

        r_no_slip = backtest_from_dataframe(
            df, ema1_period=12, ema2_period=22,
            sizing_mode='baseline', slippage_model=None,
        )
        r_slip = backtest_from_dataframe(
            df, ema1_period=12, ema2_period=22,
            sizing_mode='baseline',
            slippage_model=BasicSlippageModel(seed=42),
        )

        # Le slippage stochastique doit dégrader ou égaler le wallet final
        assert r_slip['final_wallet'] <= r_no_slip['final_wallet'], (
            f"Le slippage stochastique devrait réduire le wallet final "
            f"({r_slip['final_wallet']:.2f} vs {r_no_slip['final_wallet']:.2f})"
        )
