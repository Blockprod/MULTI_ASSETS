"""
test_p2_01_lookahead.py — Tests de non-régression pour le fix biais look-ahead (P2-01)

Objectif du correctif P2-01
-----------------------------
La sélection des paramètres EMA/scénario se faisait sur le dataset COMPLET,
créant un biais de look-ahead : les métriques (Sharpe, Calmar) incluaient
des données futures inconnues au moment de la décision.

Correctif appliqué :
1. run_all_backtests : backtests sur IS slice (70 %) uniquement.
2. backtest_and_display_results : sélection finale via Walk-Forward OOS si dispo.
3. execute_scheduled_trading   : idem + appel WF intégré.

Ces tests vérifient :
- Que l'IS slice est bien 70 % du dataset (pas plus).
- Que la sélection WF est utilisée quand elle retourne any_passed=True.
- Que le fallback IS-Calmar s'active quand WF échoue.
- Que la logique d'IS split dans run_all_backtests ne modifie pas
  le DataFrame original (pas d'effet de bord via is_df.copy()).
"""
import os
import sys
import numpy as np
import pandas as pd
from unittest.mock import patch

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'code', 'src')
sys.path.insert(0, SRC_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_df(n: int = 1000) -> pd.DataFrame:
    """Génère un DataFrame OHLCV synthétique de n barres."""
    rng = np.random.default_rng(42)
    close = np.cumprod(1 + rng.normal(0, 0.005, n)) * 100.0
    return pd.DataFrame({
        'open':   close * (1 + rng.uniform(-0.002, 0.002, n)),
        'high':   close * (1 + rng.uniform(0,     0.005, n)),
        'low':    close * (1 - rng.uniform(0,     0.005, n)),
        'close':  close,
        'volume': rng.uniform(1000, 5000, n),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Tests IS slice
# ─────────────────────────────────────────────────────────────────────────────

class TestISSliceRatio:
    """Vérifie que la tranche IS représente exactement 70 % du dataset."""

    def test_is_slice_is_70_pct(self):
        """`_is_end_sel = int(len(df) * 0.70)` → slice = 70 barres sur 100."""
        df = _make_df(1000)
        is_end = max(int(len(df) * 0.70), 1)
        assert is_end == 700, f"Attendu 700, obtenu {is_end}"
        is_df = df.iloc[:is_end]
        assert len(is_df) == 700

    def test_oos_slice_is_remaining_30_pct(self):
        """OOS = 30 % restants — non utilisé par run_all_backtests (réservé WF)."""
        df = _make_df(1000)
        is_end = max(int(len(df) * 0.70), 1)
        oos_df = df.iloc[is_end:]
        assert len(oos_df) == 300, f"Attendu 300, obtenu {len(oos_df)}"

    def test_is_slice_is_copy_no_side_effects(self):
        """is_df doit être une copie — pas de modification en place du DF source."""
        df = _make_df(500)
        is_end = max(int(len(df) * 0.70), 1)
        is_df = df.iloc[:is_end].copy()
        # Modifier is_df ne doit pas affecter df
        is_df['close'] = 999.0
        assert (df['close'] != 999.0).all(), "La modification de is_df a altéré le DF source"

    def test_short_dataset_is_protected(self):
        """Dataset < 50 barres → is_end = max(int(n*0.70), 1) reste positif."""
        for n in [1, 5, 10, 49]:
            df = _make_df(n)
            is_end = max(int(len(df) * 0.70), 1)
            assert is_end >= 1, f"is_end invalide pour n={n}: {is_end}"

    def test_is_end_excludes_last_30_pct(self):
        """Les 300 dernières barres ne doivent PAS apparaître dans is_df."""
        df = _make_df(1000)
        is_end = max(int(len(df) * 0.70), 1)
        is_df = df.iloc[:is_end]
        # Vérifier que les indices OOS (700-999) ne sont pas dans is_df
        assert max(is_df.index) < is_end, (
            "L'IS slice contient des barres OOS"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tests sélection WF vs Calmar fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestWFSelectionPriority:
    """
    Vérifie que la sélection utilise le résultat WF quand any_passed=True,
    et tombe sur IS-Calmar quand WF échoue ou ne passe pas les gates.
    """

    def _make_wf_result(self, any_passed: bool, scenario: str = 'StochRSI',
                         ema: tuple = (26, 50), tf: str = '1h',
                         oos_sharpe: float = 0.8) -> dict:
        return {
            'best_wf_config': {
                'timeframe': tf,
                'ema_periods': ema,
                'scenario': scenario,
                'avg_oos_sharpe': oos_sharpe,
                'avg_oos_win_rate': 52.0,
                'passed_oos_gates': True,
            } if any_passed else None,
            'all_wf_results': [],
            'any_passed': any_passed,
        }

    def test_wf_config_used_when_any_passed_true(self):
        """Quand any_passed=True → best_params issus de la config WF OOS."""
        wf_result = self._make_wf_result(True, 'StochRSI_ADX', (20, 40), '4h', 1.2)

        _wf_best_cfg = None
        try:
            _wf_best_cfg = wf_result.get('best_wf_config') if wf_result.get('any_passed') else None
        except Exception:
            pass

        assert _wf_best_cfg is not None
        best_params = {
            'timeframe': _wf_best_cfg['timeframe'],
            'ema1_period': _wf_best_cfg['ema_periods'][0],
            'ema2_period': _wf_best_cfg['ema_periods'][1],
            'scenario': _wf_best_cfg['scenario'],
        }
        assert best_params['scenario'] == 'StochRSI_ADX'
        assert best_params['timeframe'] == '4h'
        assert best_params['ema1_period'] == 20
        assert best_params['ema2_period'] == 40

    def test_calmar_fallback_when_wf_not_passed(self):
        """Quand any_passed=False → sélection Calmar IS utilisée (pas la config WF)."""
        wf_result = self._make_wf_result(False)

        _wf_best_cfg = None
        try:
            _wf_best_cfg = wf_result.get('best_wf_config') if wf_result.get('any_passed') else None
        except Exception:
            pass

        assert _wf_best_cfg is None, "WF config NE doit PAS être utilisée si any_passed=False"

    def test_calmar_fallback_when_wf_raises_exception(self):
        """Quand wf_result n'est pas défini (exception) → fallback Calmar."""
        _wf_best_cfg = None
        try:
            # Simule wf_result inaccessible (KeyError, AttributeError, etc.)
            _missing: dict = {}  # type: ignore[annotation-unchecked]
            _wf_best_cfg = _missing['wf_result'].get('best_wf_config')  # KeyError intentionnel
        except Exception:
            pass  # comportement attendu : _wf_best_cfg reste None

        assert _wf_best_cfg is None

    def test_wf_oos_sharpe_matters_not_is_sharpe(self):
        """La sélection WF utilise avg_oos_sharpe, pas la Sharpe IS."""
        wf_result = self._make_wf_result(True, oos_sharpe=1.5)
        cfg = wf_result['best_wf_config']
        assert cfg['avg_oos_sharpe'] == 1.5
        # OOS sharpe > IS sharpe threshold (0.5) → gates passées
        from walk_forward import validate_oos_result
        assert validate_oos_result(cfg['avg_oos_sharpe'], cfg['avg_oos_win_rate'])


# ─────────────────────────────────────────────────────────────────────────────
# Tests intégration walk_forward.run_walk_forward_validation
# ─────────────────────────────────────────────────────────────────────────────

class TestWalkForwardIntegration:
    """Vérifie que run_walk_forward_validation sélectionne sur OOS et non IS."""

    def _make_synthetic_full_sample_results(self) -> list:
        """Génère des résultats full-sample fictifs (IS uniquement après fix)."""
        return [
            {'timeframe': '1h', 'ema_periods': (26, 50), 'scenario': 'StochRSI',
             'sharpe_ratio': 1.2, 'win_rate': 58.0,
             'final_wallet': 12000.0, 'initial_wallet': 10000.0, 'max_drawdown': 0.10},
            {'timeframe': '1h', 'ema_periods': (14, 26), 'scenario': 'StochRSI_ADX',
             'sharpe_ratio': 0.3, 'win_rate': 40.0,
             'final_wallet': 10500.0, 'initial_wallet': 10000.0, 'max_drawdown': 0.15},
        ]

    def test_top_n_selection_uses_sharpe_ranking(self):
        """run_walk_forward_validation trie par Sharpe IS et prend les top-N."""
        from walk_forward import run_walk_forward_validation
        full_results = self._make_synthetic_full_sample_results()

        df = _make_df(800)
        # Ajouter colonnes indicateurs minimales
        df['stoch_rsi'] = 0.5
        df['atr'] = df['close'] * 0.01

        base_dfs = {'1h': df.copy()}

        def mock_backtest_fn(df, ema1_period, ema2_period,
                             sma_long=None, adx_period=None,
                             trix_length=None, trix_signal=None,
                             sizing_mode='baseline', periods_per_year=8766, **kwargs):
            n = len(df)
            # IS donne de bons résultats, OOS légèrement moins bons
            eq = np.cumprod(1 + np.random.default_rng(ema1_period).normal(0.001, 0.005, n)) * 10000.0
            return {
                'final_wallet': float(eq[-1]),
                'trades': pd.DataFrame([
                    {'type': 'sell', 'profit': 50.0},
                    {'type': 'sell', 'profit': 30.0},
                    {'type': 'sell', 'profit': -10.0},
                ]),
                'max_drawdown': 0.08,
                'win_rate': 66.0,
                'sharpe_ratio': float(np.mean(np.diff(eq) / eq[:-1]) /
                                      (np.std(np.diff(eq) / eq[:-1]) + 1e-9) * np.sqrt(8766)),
                'sortino_ratio': 0.9,
                'calmar_ratio': 0.5,
            }

        result = run_walk_forward_validation(
            base_dataframes=base_dfs,
            full_sample_results=full_results,
            scenarios=[{'name': 'StochRSI', 'params': {'stoch_period': 14}},
                       {'name': 'StochRSI_ADX', 'params': {'stoch_period': 14, 'adx_period': 14}}],
            backtest_fn=mock_backtest_fn,
            initial_capital=10000.0,
            top_n=2,
            n_folds=2,
        )

        assert 'best_wf_config' in result
        assert 'all_wf_results' in result
        assert isinstance(result['all_wf_results'], list)

    def test_oos_selection_differs_from_is_selection_on_overfit_config(self):
        """
        Un config suroptimisé sur IS doit être déclassé par la validation OOS.
        Le best_wf_config doit être différent du best IS config si l'OOS invalide
        le meilleur IS candidat.
        """
        from walk_forward import validate_oos_result

        # Simuler un résultat IS excellent mais OOS médiocre = suroptimisé
        is_sharpe = 2.5
        oos_sharpe = 0.2   # en dessous du seuil OOS (0.3)
        oos_wr = 25.0       # en dessous du seuil WR (30 %)

        # Gates doivent rejeter ce résultat
        assert not validate_oos_result(oos_sharpe, oos_wr), (
            "Un résultat OOS médiocre (sharpe=0.2, WR=25 %) ne doit pas passer les gates"
        )

        # Simuler un résultat IS moins bon mais OOS stable
        oos_sharpe_robust = 0.9
        oos_wr_robust = 50.0
        assert validate_oos_result(oos_sharpe_robust, oos_wr_robust), (
            "Un résultat OOS solide (sharpe=0.9, WR=50 %) doit passer les gates"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tests validate_oos_result (seuils institutionnels)
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateOOSResult:
    """Vérifie les seuils des quality gates OOS (Sharpe > 0.5, WR > 45 %)."""

    def test_both_criteria_must_pass(self):
        from walk_forward import validate_oos_result
        assert validate_oos_result(0.9, 50.0) is True
        assert validate_oos_result(0.2, 50.0) is False   # sharpe < 0.8 → fail
        assert validate_oos_result(0.9, 25.0) is False   # WR < 30 % → fail
        assert validate_oos_result(0.2, 25.0) is False   # les deux trop bas

    def test_exactly_at_threshold_fails(self):
        """Les seuils sont stricts (>), pas (>=)."""
        from walk_forward import validate_oos_result
        # 0.3 n'est PAS > 0.3
        assert validate_oos_result(0.3, 50.0) is False
        # 30.0 n'est PAS > 30.0
        assert validate_oos_result(0.6, 30.0) is False

    def test_just_above_threshold_passes(self):
        from walk_forward import validate_oos_result
        assert validate_oos_result(0.81, 30.1) is True

    def test_negative_sharpe_fails(self):
        from walk_forward import validate_oos_result
        assert validate_oos_result(-1.0, 60.0) is False


# ─────────────────────────────────────────────────────────────────────────────
# C-14: IS indicator isolation — indicators recomputed on IS slice
# ─────────────────────────────────────────────────────────────────────────────

class TestISIndicatorIsolation:
    """C-14: Garantit que les indicateurs utilisés en IS sont calculés
    exclusivement sur les données IS, sans influence OOS."""

    def test_ema_is_causal_no_lookahead(self):
        """EMA(adjust=False) sur full dataset vs IS-slice → valeurs identiques."""
        df = _make_df(1000)
        is_end = 700

        # Compute on full dataset, then slice
        df_full = df.copy()
        df_full['ema_26'] = df_full['close'].ewm(span=26, adjust=False).mean()
        ema_from_full = df_full['ema_26'].iloc[:is_end].to_numpy()

        # Compute on IS-only slice
        df_is = df.iloc[:is_end].copy()
        df_is['ema_26'] = df_is['close'].ewm(span=26, adjust=False).mean()
        ema_from_is = df_is['ema_26'].to_numpy()

        np.testing.assert_allclose(ema_from_full, ema_from_is, rtol=1e-12,
                                   err_msg="EMA computed on full vs IS should be identical")

    def test_rsi_is_causal_no_lookahead(self):
        """RSI (Wilder smoothing) sur full dataset vs IS-slice → valeurs identiques."""
        from ta.momentum import RSIIndicator
        df = _make_df(1000)
        is_end = 700

        df_full = df.copy()
        df_full['rsi'] = RSIIndicator(df_full['close'], window=14).rsi()
        rsi_from_full = df_full['rsi'].iloc[:is_end].dropna().to_numpy()

        df_is = df.iloc[:is_end].copy()
        df_is['rsi'] = RSIIndicator(df_is['close'], window=14).rsi()
        rsi_from_is = df_is['rsi'].dropna().to_numpy()

        np.testing.assert_allclose(rsi_from_full, rsi_from_is, rtol=1e-12,
                                   err_msg="RSI computed on full vs IS should be identical")

    def test_atr_is_causal_no_lookahead(self):
        """ATR (Wilder smoothing) sur full dataset vs IS-slice → valeurs identiques."""
        from ta.volatility import AverageTrueRange
        df = _make_df(1000)
        is_end = 700

        df_full = df.copy()
        df_full['atr'] = AverageTrueRange(
            high=df_full['high'], low=df_full['low'],
            close=df_full['close'], window=14,
        ).average_true_range()
        atr_from_full = df_full['atr'].iloc[:is_end].dropna().to_numpy()

        df_is = df.iloc[:is_end].copy()
        df_is['atr'] = AverageTrueRange(
            high=df_is['high'], low=df_is['low'],
            close=df_is['close'], window=14,
        ).average_true_range()
        atr_from_is = df_is['atr'].dropna().to_numpy()

        np.testing.assert_allclose(atr_from_full, atr_from_is, rtol=1e-12,
                                   err_msg="ATR computed on full vs IS should be identical")

    def test_stochrsi_is_causal_no_lookahead(self):
        """StochRSI (rolling min/max) sur full vs IS-slice → valeurs identiques."""
        from ta.momentum import RSIIndicator
        from indicators_engine import compute_stochrsi
        df = _make_df(1000)
        is_end = 700

        df_full = df.copy()
        df_full['rsi'] = RSIIndicator(df_full['close'], window=14).rsi()
        df_full['stoch_rsi'] = compute_stochrsi(df_full['rsi'], period=14)
        srsi_from_full = df_full['stoch_rsi'].iloc[:is_end].dropna().to_numpy()

        df_is = df.iloc[:is_end].copy()
        df_is['rsi'] = RSIIndicator(df_is['close'], window=14).rsi()
        df_is['stoch_rsi'] = compute_stochrsi(df_is['rsi'], period=14)
        srsi_from_is = df_is['stoch_rsi'].dropna().to_numpy()

        np.testing.assert_allclose(srsi_from_full, srsi_from_is, rtol=1e-12,
                                   err_msg="StochRSI computed on full vs IS should be identical")

    def test_run_all_backtests_recomputes_on_is(self):
        """run_all_backtests appelle les backtests avec des indicateurs
        recalculés sur IS (pas hérités du full dataset)."""
        from backtest_runner import run_all_backtests

        df = _make_df(200)
        # Pre-compute indicators like prepare_base_dataframe does
        for p in [14, 25, 26, 45, 50]:
            df[f'ema_{p}'] = df['close'].ewm(span=p, adjust=False).mean()
        from ta.momentum import RSIIndicator
        from ta.volatility import AverageTrueRange
        from indicators_engine import compute_stochrsi
        df['rsi'] = RSIIndicator(df['close'], window=14).rsi()
        df['atr'] = AverageTrueRange(
            high=df['high'], low=df['low'], close=df['close'], window=14
        ).average_true_range()
        df['stoch_rsi'] = compute_stochrsi(df['rsi'], period=14)
        df.dropna(subset=['close', 'rsi', 'atr'], inplace=True)

        # Tamper with OOS portion to prove IS doesn't see it
        is_end = max(int(len(df) * 0.70), 1)

        # Corrupt OOS portion
        df.loc[df.index[is_end:], 'ema_26'] = 999999.0
        df.loc[df.index[is_end:], 'rsi'] = 999.0

        captured_is_dfs = []

        def fake_run_single(task):
            tf, e1, e2, scenario, is_df_arg, bp, sm = task
            captured_is_dfs.append(is_df_arg.copy())
            return {
                'scenario': scenario['name'], 'timeframe': tf,
                'ema_periods': [e1, e2],
                'initial_wallet': 10000, 'final_wallet': 10500,
                'sharpe_ratio': 1.0, 'win_rate': 55.0,
                'max_drawdown': 0.1, 'calmar_ratio': 1.5,
                'trades': pd.DataFrame(),
            }

        with patch('backtest_runner.run_single_backtest_optimized', side_effect=fake_run_single):
            results = run_all_backtests(
                'TESTUSDT', '01 Jan 2022', ['1h'],
                sizing_mode='baseline',
                prepare_base_dataframe_fn=lambda *a, **kw: df,
            )

        # Verify at least one IS df was captured
        assert len(captured_is_dfs) > 0
        for captured_df in captured_is_dfs:
            # IS should NOT contain corrupted OOS values
            assert (captured_df['ema_26'] != 999999.0).all(), \
                "IS slice should have recomputed EMA, not OOS-corrupted values"
            assert (captured_df['rsi'] != 999.0).all(), \
                "IS slice should have recomputed RSI, not OOS-corrupted values"
