"""backtest_orchestrator.py — C-03 Phase 3

Fonctions extraites de MULTI_SYMBOLS.py (apply_oos_quality_gate, execute_scheduled_trading,
execute_live_trading_only, backtest_and_display_results).

Toutes les fonctions reçoivent un _BacktestDeps injecté par des wrappers dans MULTI_SYMBOLS.py.
Cela permet aux tests de continuer à patcher via monkeypatch.setattr(ms, ...) sans modification.
"""

import logging
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, cast

from rich.console import Console
from rich.panel import Panel
from exchange_client import ExchangePort

logger = logging.getLogger(__name__)

_LIVE_ONLY_POST_SCHEDULED_SKIP_SECONDS = 90.0
_KRAKEN_QUOTES = ("USDC", "USDT", "USD", "EUR", "GBP", "CAD", "AUD")


def _wf_fold_label(config: Any) -> str:
    folds = int(getattr(config, 'oos_min_folds', 3) or 3)
    return f"{folds}/{folds}"


def _kraken_symbol_fallback(symbol: str) -> str:
    normalized = str(symbol or "").replace("/", "").upper()
    for quote in _KRAKEN_QUOTES:
        if normalized.endswith(quote) and len(normalized) > len(quote):
            base = normalized[: -len(quote)]
            if base == "BTC":
                base = "XBT"
            return f"{base}/{quote}"
    return str(symbol)


def _wf_status_and_reason(wf_result: Mapping[str, Any]) -> Tuple[str, str]:
    if wf_result.get('any_passed'):
        return 'validated', ''
    if wf_result.get('reject_reason'):
        return 'data_insufficient', str(wf_result.get('reject_reason'))
    if wf_result.get('wf_timeout'):
        return 'data_insufficient', 'optuna_timeout'
    diagnostics = wf_result.get('timeframe_diagnostics')
    eligible = list(wf_result.get('eligible_timeframes') or [])
    if isinstance(diagnostics, Mapping):
        rejected = [
            str(value.get('reject_reason'))
            for value in diagnostics.values()
            if isinstance(value, Mapping) and value.get('reject_reason')
        ]
    else:
        rejected = []
    if eligible:
        return 'oos_failed', "OOS gates non validés pour " + ", ".join(str(tf) for tf in eligible)
    if rejected:
        return 'data_insufficient', "; ".join(rejected)
    return 'data_insufficient', "aucun timeframe n'a produit les folds WF requis"


def _mark_wf_blocked(
    pair_state: Dict[str, Any],
    entries_ready: Dict[str, bool],
    pair: str,
    wf_result: Mapping[str, Any],
    *,
    log_tag: str,
) -> None:
    status, reason = _wf_status_and_reason(wf_result)
    if status == 'validated':
        return
    pair_state['wf_status'] = status
    pair_state['wf_block_reason'] = reason
    pair_state['wf_last_attempt_at'] = datetime.now(timezone.utc).isoformat()
    pair_state['entries_ready'] = False
    pair_state['wf_fallback'] = True
    pair_state['runtime_phase'] = 'protection_only'
    pair_state['live_execution_mode'] = 'PROTECTION_ONLY'
    pair_state['wf_session_status'] = 'not_validated'
    entries_ready[pair] = False
    event = 'WF_BLOCKED_OOS' if status == 'oos_failed' else 'WF_BLOCKED_DATA'
    logger.warning("[%s] %s %s — %s", log_tag, event, pair, reason)


def _has_console_output(console_obj: Any) -> bool:
    """Return True when Rich console has a writable output stream.

    In pythonw mode on Windows, stdout/stderr can be None. Rich then raises
    errors such as "NoneType has no attribute flush" when printing panels.
    """
    try:
        out = getattr(sys, 'stdout', None)
        if not (out and hasattr(out, 'write') and hasattr(out, 'flush')):
            return False
        file_obj = getattr(console_obj, 'file', None)
        return bool(file_obj and hasattr(file_obj, 'write') and hasattr(file_obj, 'flush'))
    except Exception:
        return False


def _safe_stdout_flush() -> None:
    """Flush stdout only when a real writable stream exists."""
    try:
        out = getattr(sys, 'stdout', None)
        if out and hasattr(out, 'write') and hasattr(out, 'flush'):
            out.flush()
    except Exception:
        pass


# ─── Injection de dépendances ─────────────────────────────────────────────────

def _live_panel_enabled(deps: '_BacktestDeps') -> bool:
    enabled_fn = getattr(deps, 'live_panel_enabled_fn', None)
    if enabled_fn is None:
        return True
    try:
        return bool(enabled_fn())
    except Exception:
        return True


def _broker_display_symbol(
    deps: '_BacktestDeps',
    backtest_pair: str,
    real_trading_pair: str,
    pair_state: Mapping[str, Any],
) -> str:
    broker_symbol = pair_state.get('broker_symbol')
    if broker_symbol:
        return str(broker_symbol)
    resolver = getattr(deps.client, 'resolve_pair', None)
    if callable(resolver):
        try:
            resolved = resolver(backtest_pair)
            resolved_symbol = getattr(resolved, 'broker_symbol', None)
            if resolved_symbol:
                return str(resolved_symbol)
        except Exception:
            pass
    broker_name = str(getattr(deps.client, 'broker', '')).upper()
    if broker_name == 'KRAKEN':
        return _kraken_symbol_fallback(backtest_pair or real_trading_pair)
    fallback = _kraken_symbol_fallback(backtest_pair or real_trading_pair)
    if fallback != (backtest_pair or real_trading_pair):
        return fallback
    return real_trading_pair


@dataclass
class _BacktestDeps:
    """Dépendances injectées dans les fonctions de l'orchestrateur backtest (C-03 Phase 3)."""
    # Core state
    bot_state: Dict[str, Any]
    bot_state_lock: Any                             # threading.RLock
    config: Any                                     # Config singleton
    client: ExchangePort                            # BinanceFinalClient (ExchangePort structurellement)
    console: Any                                    # Rich Console (global)
    timeframes: List[str]
    schedule: Any                                   # schedule module
    # Callables
    save_fn: Callable                               # save_bot_state
    send_alert_fn: Callable                         # send_trading_alert_email
    send_email_alert_fn: Callable                   # send_email_alert
    execute_trades_fn: Callable                     # execute_real_trades
    run_all_backtests_fn: Callable                  # run_all_backtests
    prepare_base_dataframe_fn: Callable             # prepare_base_dataframe
    display_results_fn: Callable                    # display_results_for_pair
    display_execution_header_fn: Callable           # display_execution_header
    build_tracking_panel_fn: Callable               # build_tracking_panel
    display_market_changes_fn: Callable             # display_market_changes
    detect_market_changes_fn: Callable              # detect_market_changes
    display_backtest_table_fn: Callable             # display_backtest_table
    backtest_from_dataframe_fn: Callable            # backtest_from_dataframe
    select_best_by_calmar_fn: Callable              # _select_best_by_calmar
    make_default_pair_state_fn: Callable            # _make_default_pair_state
    build_snapshot_fn: Callable                     # StrategySnapshot.from WF result
    publish_snapshot_fn: Callable                   # persist then expose to live
    parity_backtest_fn: Callable                    # exact snapshot full-sample audit
    # Mutable shared state (same objects as MULTI_SYMBOLS globals — passed by reference)
    last_backtest_time: Dict[str, float]            # _last_backtest_time
    live_best_params: Dict[str, Dict[str, Any]]     # _live_best_params
    entries_ready: Dict[str, bool]                  # session-only WF readiness
    last_scheduled_trade_time: Dict[str, float]     # _last_scheduled_trade_time
    oos_alert_last_sent: Dict[str, float]           # _oos_alert_last_sent
    oos_alert_lock: Any                             # threading.Lock
    # Constants
    wf_scenarios: List[Dict[str, Any]]              # WF_SCENARIOS
    scenario_default_params: Dict[str, Dict[str, Any]]  # SCENARIO_DEFAULT_PARAMS
    live_panel_enabled_fn: Optional[Callable[[], bool]] = None
    describe_live_mode_fn: Optional[Callable[[str, Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]] = None


# ─── Fonctions extraites ──────────────────────────────────────────────────────

def run_stoch_threshold_grid_search(
    is_results: List[Dict[str, Any]],
    base_dataframes: Dict[str, Any],
    backtest_fn: Callable,
    scenario_default_params: Dict[str, Dict[str, Any]],
    sizing_mode: str = 'risk',
    *,
    n_top: int = 5,
) -> "Optional[Dict[str, Any]]":
    """Grid search sur buy_min × buy_max × sell_exit avec les n_top meilleurs configs IS.

    Utilise les résultats IS (full-sample) comme proxy pour trouver les seuils
    StochRSI optimaux, indépendamment des EMAs sélectionnées.

    Parameters
    ----------
    is_results : list[dict]
        Résultats IS complets (sortie de run_all_backtests).
    base_dataframes : dict[str, DataFrame]
        DataFrames par timeframe, déjà préparés (colonnes indicateurs incluses).
    backtest_fn : callable
        ``backtest_from_dataframe`` avec support des overrides stoch.
    scenario_default_params : dict
        Mapping nom_scénario → params (ex. {'StochRSI_ADX': {'adx_period': 14}}).
    sizing_mode : str
        Mode de position sizing à utiliser.
    n_top : int
        Nombre de configs IS à utiliser comme proxy (défaut 5).

    Returns
    -------
    dict or None
        ``{'buy_min', 'buy_max', 'sell_exit', 'avg_calmar', 'n_valid'}``
        ou None si aucun résultat valide.
    """
    import numpy as _np  # local import — évite shadowing au module level

    BUY_MIN_GRID   = [0.02, 0.05, 0.08, 0.10, 0.15]
    BUY_MAX_GRID   = [0.70, 0.75, 0.80, 0.85]
    SELL_EXIT_GRID = [0.30, 0.40, 0.50]

    # Top N configs triées par calmar_ratio décroissant
    sorted_results = sorted(is_results, key=lambda r: r.get('calmar_ratio', 0.0), reverse=True)
    top_configs = sorted_results[:n_top]

    if not top_configs:
        logger.warning("[STOCH-OPT] Aucun résultat IS disponible pour le grid search.")
        return None

    best_score = -_np.inf
    best_combo: "Optional[Dict[str, Any]]" = None
    total_combos = sum(
        1 for bmin in BUY_MIN_GRID for bmax in BUY_MAX_GRID if bmin < bmax
    ) * len(SELL_EXIT_GRID)
    logger.info(
        "[STOCH-OPT] Grid search: %d combos × top-%d configs IS = %d backtests",
        total_combos, len(top_configs), total_combos * len(top_configs),
    )

    for buy_min in BUY_MIN_GRID:
        for buy_max in BUY_MAX_GRID:
            if buy_min >= buy_max:
                continue
            for sell_exit in SELL_EXIT_GRID:
                total_calmar = 0.0
                valid_runs = 0

                for cfg in top_configs:
                    tf = cfg.get('timeframe', '')
                    ema_periods = cfg.get('ema_periods', (26, 50))
                    ema1, ema2 = ema_periods[0], ema_periods[1]
                    scenario_name = cfg.get('scenario', 'StochRSI')
                    sc_params = scenario_default_params.get(scenario_name, {})

                    df = base_dataframes.get(tf)
                    if df is None or (hasattr(df, 'empty') and df.empty):
                        continue

                    try:
                        result = backtest_fn(
                            df=df,
                            ema1_period=ema1,
                            ema2_period=ema2,
                            sma_long=sc_params.get('sma_long'),
                            adx_period=sc_params.get('adx_period'),
                            trix_length=sc_params.get('trix_length'),
                            trix_signal=sc_params.get('trix_signal'),
                            sizing_mode=sizing_mode,
                            stoch_buy_min_override=buy_min,
                            stoch_buy_max_override=buy_max,
                            stoch_sell_exit_override=sell_exit,
                        )
                        calmar = result.get('calmar_ratio', 0.0)
                        if calmar > 0:
                            total_calmar += calmar
                            valid_runs += 1
                    except Exception as _e:
                        logger.debug("[STOCH-OPT] Backtest erreur (buy_min=%.2f buy_max=%.2f sell=%.2f): %s", buy_min, buy_max, sell_exit, _e)

                score = total_calmar / valid_runs if valid_runs > 0 else 0.0
                if score > best_score:
                    best_score = score
                    best_combo = {
                        'buy_min': buy_min,
                        'buy_max': buy_max,
                        'sell_exit': sell_exit,
                        'avg_calmar': score,
                        'n_valid': valid_runs,
                    }

    if best_combo is not None:
        logger.info(
            "[STOCH-OPT] Meilleurs seuils: buy_min=%.2f buy_max=%.2f sell_exit=%.2f "
            "— Calmar moyen=%.3f (%d configs valides)",
            best_combo['buy_min'], best_combo['buy_max'], best_combo['sell_exit'],
            best_combo['avg_calmar'], best_combo['n_valid'],
        )
    return best_combo


def _apply_oos_quality_gate(
    results: List[Dict[str, Any]],
    pair: str,
    deps: '_BacktestDeps',
    *,
    log_tag: str = "C-13",
    unblock_on_pass: bool = True,
    send_alert: bool = False,
    save_force: bool = False,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Filtre *results* par les OOS quality gates et met à jour bot_state.

    P2-05: logique extraite de 3 sites dupliqués (SCHEDULED, MAIN, MAIN-LOOP).

    Returns
    -------
    (selection_pool, oos_blocked)
        *selection_pool* est le sous-ensemble OOS-valide, ou tout le pool en dégradé.
        *oos_blocked* est True si aucun résultat n'a passé les gates.
    """
    # Filtre configs dégénérées : WR=100% + DD=0% simultanément → physiquement impossible sur 3 ans
    _n_before = len(results)
    results = [
        r for r in results
        if not (r.get('win_rate', 0.0) >= 100.0 and r.get('max_drawdown', 1.0) == 0.0)
    ]
    if len(results) < _n_before:
        logger.warning(
            "[%s] %d config(s) dégénérées exclues (WR=100%% + DD=0%%)",
            log_tag, _n_before - len(results),
        )

    try:
        from walk_forward import validate_oos_result as _validate_oos
        valid = [
            r for r in results
            if _validate_oos(r.get('sharpe_ratio', 0.0), r.get('win_rate', 0.0))
        ]
    except Exception as _imp_err:
        logger.warning("[%s] validate_oos_result indisponible: %s", log_tag, _imp_err)
        valid = []  # situation dégradée → bloquer

    if valid:
        pool = valid
        blocked = False
        if unblock_on_pass:
            with deps.bot_state_lock:
                ps = deps.bot_state.setdefault(pair, {})
                was_blocked = ps.pop('oos_blocked', None) is not None
                ps.pop('oos_blocked_since', None)
            if was_blocked:
                deps.save_fn()
                logger.info(
                    "[%s] Blocage P0-03 levé — %d/%d résultats passent les IS quality gates.",
                    log_tag, len(valid), len(results),
                )
            else:
                logger.info(
                    "[%s] %d/%d résultats passent les IS quality gates.",
                    log_tag, len(valid), len(results),
                )
        else:
            logger.info(
                "[%s] %d/%d résultats passent les IS quality gates.",
                log_tag, len(valid), len(results),
            )
    else:
        pool = results
        blocked = True
        _strict = getattr(deps.config, 'oos_strict_mode', True)
        if _strict:
            with deps.bot_state_lock:
                ps = deps.bot_state.setdefault(pair, {})
                ps['oos_blocked'] = True
                ps['oos_blocked_since'] = time.time()
            deps.save_fn(force=save_force)
            logger.warning(
                "[%s] Aucun résultat ne passe les OOS gates "
                "(Sharpe > %.2f & WR > %.0f%%) — ACHATS BLOQUÉS pour %s.",
                log_tag, deps.config.oos_sharpe_min, deps.config.oos_win_rate_min, pair,
            )
        else:
            logger.warning(
                "[%s] Aucun résultat ne passe les OOS gates "
                "(Sharpe > %.2f & WR > %.0f%%) — mode souple, achats maintenus pour %s.",
                log_tag, deps.config.oos_sharpe_min, deps.config.oos_win_rate_min, pair,
            )
        if send_alert:
            logger.info(
                "[%s] Alerte email OOS desactivee pour %s; achats bloques, stops actifs.",
                log_tag,
                pair,
            )

    return pool, blocked


def _execute_scheduled_trading(
    real_trading_pair: str,
    time_interval: str,
    best_params: Dict[str, Any],
    backtest_pair: str,
    sizing_mode: str,
    deps: '_BacktestDeps',
) -> None:
    """Wrapper pour les exécutions planifiées avec affichage complet (identique au démarrage)."""
    try:
        # === MESSAGE VISUEL DE DEMARRAGE ===
        logger.info(f"[SCHEDULED] DEBUT execution planifiee - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        deps.display_execution_header_fn(backtest_pair, real_trading_pair, time_interval, deps.console)

        # Force flush de la console (si flux disponible)
        _safe_stdout_flush()
        logger.info("[SCHEDULED] Header affiché, debut des backtests...")

        # Re-faire le backtest pour obtenir les paramètres les plus à jour
        # THROTTLE: ne re-backtester que toutes les heures (pas à chaque cycle de 2 min)
        _now = time.time()
        with deps.bot_state_lock:  # P1-06: protéger _last_backtest_time contre accès concurrents
            _last_bt = deps.last_backtest_time.get(backtest_pair, 0)
        _time_since_last = _now - _last_bt

        if _time_since_last < deps.config.backtest_throttle_seconds:
            _remaining = int((deps.config.backtest_throttle_seconds - _time_since_last) / 60)
            logger.info(f"[SCHEDULED] Backtest throttlé pour {backtest_pair} — prochain dans ~{_remaining} min. Utilisation des anciens paramètres.")
        else:
            logger.info(f"[SCHEDULED] Re-backtest de {backtest_pair} pour obtenir les paramètres les plus à jour...")

            # Calculer les dates dynamiquement
            today = datetime.today()
            dynamic_start_date = (today - timedelta(days=deps.config.backtest_days)).strftime("%d %B %Y")
            logger.info(f"[SCHEDULED] Backtest dates: {dynamic_start_date} -> {today.strftime('%d %B %Y')}")

            # Re-exécuter le backtest et AFFICHER les résultats
            logger.info("[SCHEDULED] Lancement des backtests...")
            try:
                backtest_results = deps.run_all_backtests_fn(
                    backtest_pair,
                    dynamic_start_date,
                    deps.timeframes,
                    sizing_mode=sizing_mode,
                    stoch_thresholds=best_params,
                )
            except Exception as backtest_err:
                logger.error(f"[SCHEDULED] ERREUR backtest {backtest_pair}: {backtest_err}")
                logger.error(f"[SCHEDULED] Traceback backtest: {traceback.format_exc()}")
                deps.console.print(f"[red][SCHEDULED] Erreur backtest {backtest_pair} : {backtest_err}[/red]")
                backtest_results = None

            if backtest_results:
                with deps.bot_state_lock:  # P1-06
                    deps.last_backtest_time[backtest_pair] = time.time()
                logger.info(f"[SCHEDULED] {len(backtest_results)} resultats de backtest recus")

                # C-07 + C-13 + P2-05: OOS quality gate centralisée
                _selection_pool, _ = _apply_oos_quality_gate(
                    backtest_results, backtest_pair, deps,
                    log_tag="SCHEDULED C-13", send_alert=False,
                )

                # P2-01: Walk-Forward OOS validation pour la sélection planifiée.
                # ML-07: Optuna bayésien en priorité, fallback vers grid WF.
                _sched_wf_best = None
                _wf_res_sched: Dict[str, Any] = {}
                try:
                    from walk_forward import run_walk_forward_optuna as _run_wf_optuna
                    _wf_dfs_sched = {}
                    for _tf_s in deps.timeframes:
                        _df_s = deps.prepare_base_dataframe_fn(backtest_pair, _tf_s, dynamic_start_date, 14)
                        _wf_dfs_sched[_tf_s] = _df_s if _df_s is not None and not _df_s.empty else __import__('pandas').DataFrame()

                    logger.info(
                        "[SCHEDULED STOCH] Seuils optimisés dans les folds IS Optuna; "
                        "grid full-sample exclu du pipeline LIVE."
                    )

                    # ML-07: Try Optuna first (wider EMA search space)
                    _wf_res_sched = _run_wf_optuna(
                        base_dataframes=_wf_dfs_sched,
                        scenarios=deps.wf_scenarios,
                        backtest_fn=deps.backtest_from_dataframe_fn,
                        initial_capital=deps.config.initial_wallet,
                        sizing_mode=sizing_mode,
                        n_folds=getattr(deps.config, 'oos_min_folds', 4),
                        n_trials=100,
                        required_folds=getattr(deps.config, 'oos_min_folds', 4),
                        progress_pair=backtest_pair,
                        timeout_seconds=getattr(deps.config, 'wf_optuna_timeout_seconds', 1800),
                        progress_trials=getattr(deps.config, 'wf_optuna_progress_trials', 10),
                        progress_seconds=getattr(deps.config, 'wf_optuna_progress_seconds', 60),
                    )
                    if _wf_res_sched.get('any_passed'):
                        _sched_wf_best = _wf_res_sched['best_wf_config']
                        logger.info(
                            "[SCHEDULED P2-01] Sélection Walk-Forward OOS (%s): %s EMA(%s,%s) %s — "
                            "OOS Sharpe=%.2f.",
                            _wf_res_sched.get('method', 'grid'),
                            _sched_wf_best['scenario'],
                            _sched_wf_best['ema_periods'][0],
                            _sched_wf_best['ema_periods'][1],
                            _sched_wf_best['timeframe'],
                            _sched_wf_best.get('avg_oos_sharpe', 0.0),
                        )
                        logger.info(
                            "[SCHEDULED-WF] WF_VALIDATED %s — publication snapshot requise avant achats.",
                            backtest_pair,
                        )
                    else:
                        logger.info(
                            "[SCHEDULED P2-01] Aucun résultat Optuna WF %s valide — entrées bloquées.",
                            _wf_fold_label(deps.config),
                        )
                except Exception as _wf_sched_err:
                    logger.warning("[SCHEDULED P2-01] WF validation skipped: %s", _wf_sched_err)

                best_result = deps.select_best_by_calmar_fn(_selection_pool)
                best_profit = best_result['final_wallet'] - best_result['initial_wallet']

                logger.info(
                    "[SCHEDULED] Meilleur resultat IS (Calmar, pool OOS=%d configs): %s sur %s | Profit IS: $%s",
                    len(_selection_pool), best_result['scenario'], best_result['timeframe'], f"{best_profit:,.2f}",
                )

                # === AFFICHAGE DES RESULTATS ===
                try:
                    deps.display_results_fn(backtest_pair, backtest_results, wf_config=_sched_wf_best)
                    logger.info(f"[SCHEDULED] Résultats affichés pour {backtest_pair}")
                    _safe_stdout_flush()
                except Exception as display_err:
                    logger.error(f"[SCHEDULED] Erreur affichage résultats: {str(display_err)}")

                # Only a strict OOS snapshot may be published to LIVE.
                if _sched_wf_best:
                    _snapshot = deps.build_snapshot_fn(backtest_pair, _sched_wf_best, _wf_dfs_sched)
                    if deps.publish_snapshot_fn(_snapshot):
                        updated_best_params = _snapshot.as_best_params()
                        with deps.bot_state_lock:
                            pair_state = deps.bot_state.setdefault(backtest_pair, {})
                            pair_state['wf_status'] = 'validated'
                            pair_state['wf_block_reason'] = None
                            pair_state['wf_last_attempt_at'] = datetime.now(timezone.utc).isoformat()
                            pair_state['entries_ready'] = True
                            pair_state['wf_fallback'] = False
                            deps.entries_ready[backtest_pair] = True
                        deps.parity_backtest_fn(_snapshot, _wf_dfs_sched, sizing_mode)
                    else:
                        updated_best_params = dict(best_params)
                        _sched_wf_best = None
                else:
                    updated_best_params = dict(best_params)
                    with deps.bot_state_lock:
                        pair_state = deps.bot_state.setdefault(backtest_pair, {})
                        _mark_wf_blocked(
                            pair_state,
                            deps.entries_ready,
                            backtest_pair,
                            _wf_res_sched,
                            log_tag='SCHEDULED-WF',
                        )
                    deps.save_fn(force=True)

                # Vérifier si les paramètres ont changé
                if updated_best_params != best_params:
                    logger.info(f"[SCHEDULED] CHANGEMENT DETECTE - Anciens params: {best_params}")
                    logger.info(f"[SCHEDULED] Nouveaux params: {updated_best_params}")
                    best_params = updated_best_params
                else:
                    logger.info(f"[SCHEDULED] Parametres inchanges pour {backtest_pair}")
            else:
                logger.warning(f"[SCHEDULED] Aucun resultat de backtest pour {backtest_pair}, utilisation des anciens parametres")
                deps.console.print(f"[yellow][SCHEDULED] Aucun résultat de backtest pour {backtest_pair} – affichage sauté[/yellow]")
                # C-06: alerte email sur échec backtest — le bot continue avec anciens params
                try:
                    deps.send_alert_fn(
                        subject=f"[ALERTE] Backtest échoué pour {backtest_pair}",
                        body_main=(
                            f"Le backtest de {backtest_pair} n'a retourné aucun résultat.\n"
                            f"Le bot continue avec les anciens paramètres: {best_params}\n\n"
                            f"Vérifier les logs pour plus de détails."
                        ),
                        client=deps.client,
                    )
                except Exception as _alert_err:
                    logger.error(f"[SCHEDULED] Envoi alerte échec backtest impossible: {_alert_err}")

        logger.info("[SCHEDULED] Calcul terminé; le worker Live reste l'unique exécuteur d'ordres.")

        # === AFFICHAGE PANEL - SUIVI & PLANIFICATION ===
        logger.info("[SCHEDULED] Affichage des informations de suivi...")

        # Assurer l'initialisation par defaut de l'etat de la paire
        pair_state = cast(Dict[str, Any], deps.bot_state.setdefault(backtest_pair, {}))
        # IMPORTANT : Ne pas réinitialiser last_order_side s'il existe déjà
        if 'last_order_side' not in pair_state:
            pair_state['last_order_side'] = None
        current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Afficher le panel de suivi (avant last_run_time update — P4.2: fix "Temps écoulé 0:00:00")
        logger.info("[SCHEDULED] Création et affichage du panel de suivi...")
        try:
            if _has_console_output(deps.console) and _live_panel_enabled(deps):
                deps.console.print(deps.build_tracking_panel_fn(pair_state, current_run_time))
                deps.console.print("\n")
                _safe_stdout_flush()
            logger.info(f"[SCHEDULED] Exécution planifiée COMPLETEE pour {backtest_pair}")
        except Exception as tracking_err:
            logger.error(f"[SCHEDULED] Erreur affichage tracking panel: {str(tracking_err)}")

        # P4.2: mettre à jour last_run_time APRES le panel
        pair_state['last_run_time'] = current_run_time
        pair_state['last_execution'] = current_run_time
        deps.save_fn()

    except Exception as e:
        logger.error(f"[SCHEDULED] Erreur GLOBALE execution planifiee {backtest_pair}: {str(e)}")
        logger.error(f"[SCHEDULED] Traceback complet: {traceback.format_exc()}")
        try:
            deps.send_alert_fn(
                subject=f"[CRITIQUE P1] Erreur globale scheduled — {backtest_pair}",
                body_main=(
                    f"La tâche planifiée (backtest+WF+trade) a planté globalement.\n\n"
                    f"Paire : {backtest_pair}\n"
                    f"Erreur : {type(e).__name__}: {str(e)[:300]}\n\n"
                    f"Traceback (tronqué) :\n{traceback.format_exc()[:500]}\n\n"
                    f"Le bot continue mais cette exécution a été ignorée."
                ),
                client=deps.client,
            )
        except Exception as _e:
            logger.warning("[SCHEDULED] Email alerte globale impossible: %s", _e)


def _execute_live_trading_only(
    real_trading_pair: str,
    backtest_pair: str,
    sizing_mode: str,
    deps: '_BacktestDeps',
) -> None:
    """Exécution live uniquement sans backtest — planifiée toutes les 2 minutes.

    Lit _live_best_params (mis à jour par execute_scheduled_trading toutes les heures)
    et appelle directement execute_real_trades sans aucun backtest ni WF.
    """
    try:
        with deps.bot_state_lock:
            current_params = dict(deps.live_best_params.get(backtest_pair, {}))
            current_params['_entries_ready'] = bool(deps.entries_ready.get(backtest_pair, False))
            last_scheduled_trade_time = deps.last_scheduled_trade_time.get(backtest_pair, 0.0)
        if not current_params or 'timeframe' not in current_params:
            logger.warning(f"[LIVE-ONLY] {backtest_pair}: paramètres non disponibles, skip.")
            return

        seconds_since_scheduled = time.time() - last_scheduled_trade_time if last_scheduled_trade_time else None
        if seconds_since_scheduled is not None and seconds_since_scheduled < _LIVE_ONLY_POST_SCHEDULED_SKIP_SECONDS:
            logger.info(
                "[LIVE-ONLY] %s: cycle ignoré — exécution planifiée terminée il y a %.0fs.",
                backtest_pair,
                seconds_since_scheduled,
            )
            return

        tf = current_params['timeframe']
        with deps.bot_state_lock:
            # C-06: 'in_position' supprimé du bot_state — utiliser last_order_side == 'BUY'
            _pair_state_snap = dict(deps.bot_state.get(backtest_pair, {}))
            _in_position = _pair_state_snap.get('last_order_side') == 'BUY'
            _broker_symbol = _broker_display_symbol(
                deps, backtest_pair, real_trading_pair, _pair_state_snap
            )
        logger.debug(
            "[LIVE-ONLY-DBG] %s: in_position=%r (clés pair_state: %s)",
            backtest_pair,
            _in_position,
            sorted(_pair_state_snap.keys()),
        )
        if _in_position:
            # F-COH: afficher les params d'ENTRÉE verrouillés, pas les params WF actuels
            _entry_scenario = _pair_state_snap.get('entry_scenario', current_params.get('scenario'))
            _entry_ema1     = _pair_state_snap.get('entry_ema1', current_params.get('ema1_period'))
            _entry_ema2     = _pair_state_snap.get('entry_ema2', current_params.get('ema2_period'))
            _entry_tf       = _pair_state_snap.get('entry_timeframe', tf)
            logger.info(
                "[LIVE-ONLY] %s -> %s @ %s — %s EMA(%s/%s) %s [F-COH: verrouillé sur entrée | WF\u2192 %s EMA(%s/%s) %s]",
                backtest_pair, _broker_symbol, datetime.now().strftime('%H:%M:%S'),
                _entry_scenario, _entry_ema1, _entry_ema2, _entry_tf,
                current_params.get('scenario'), current_params.get('ema1_period'),
                current_params.get('ema2_period'), tf,
            )
        else:
            logger.info(
                "[LIVE-ONLY] %s -> %s @ %s — %s EMA(%s/%s) %s",
                backtest_pair, _broker_symbol, datetime.now().strftime('%H:%M:%S'),
                current_params.get('scenario'), current_params.get('ema1_period'),
                current_params.get('ema2_period'), tf,
            )

        try:
            deps.execute_trades_fn(real_trading_pair, tf, current_params, backtest_pair, sizing_mode=sizing_mode)
        except Exception as trade_err:
            logger.error(f"[LIVE-ONLY] Erreur trading {backtest_pair}: {trade_err}")
            logger.error(f"[LIVE-ONLY] Traceback: {traceback.format_exc()}")
            try:
                deps.send_alert_fn(
                    subject=f"[ALERTE P1] Erreur live-only — {backtest_pair}",
                    body_main=(
                        f"La tâche live-only (2 min) a planté.\n\n"
                        f"Paire : {backtest_pair}\n"
                        f"Erreur : {type(trade_err).__name__}: {str(trade_err)[:300]}\n\n"
                        f"Traceback (tronqué) :\n{traceback.format_exc()[:500]}\n\n"
                        f"Le bot continue mais ce cycle de trading a été ignoré."
                    ),
                    client=deps.client,
                )
            except Exception as _e:
                logger.warning("[LIVE-ONLY] Email alerte impossible: %s", _e)

        # Always update last_execution — even on trade error (D-10: dashboard Last Cycle)
        pair_state = cast(Dict[str, Any], deps.bot_state.setdefault(backtest_pair, {}))
        current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # P4.2: afficher panel AVANT last_run_time update (fix "Temps écoulé 0:00:00")
        try:
            if _has_console_output(deps.console) and _live_panel_enabled(deps):
                deps.console.print(deps.build_tracking_panel_fn(pair_state, current_run_time))
                deps.console.print("\n")
                _safe_stdout_flush()
        except Exception as _panel_err:
            logger.error(f"[LIVE-ONLY] Erreur panel tracking: {_panel_err}")

        pair_state['last_run_time'] = current_run_time
        pair_state['last_execution'] = current_run_time
        deps.save_fn()

    except Exception as e:
        logger.error(f"[LIVE-ONLY] Erreur {backtest_pair}: {e}")
        logger.error(f"[LIVE-ONLY] Traceback: {traceback.format_exc()}")
        try:
            deps.send_alert_fn(
                subject=f"[ALERTE P1] Erreur live-only — {backtest_pair}",
                body_main=(
                    f"La tâche live-only (2 min) a planté.\n\n"
                    f"Paire : {backtest_pair}\n"
                    f"Erreur : {type(e).__name__}: {str(e)[:300]}\n\n"
                    f"Traceback (tronqué) :\n{traceback.format_exc()[:500]}\n\n"
                    f"Le bot continue mais ce cycle de trading a été ignoré."
                ),
                client=deps.client,
            )
        except Exception as _e:
            logger.warning("[LIVE-ONLY] Email alerte impossible: %s", _e)


def _backtest_and_display_results(
    backtest_pair: str,
    real_trading_pair: str,
    _start_date: str,
    _timeframes: List[str],
    sizing_mode: str,
    deps: '_BacktestDeps',
) -> None:
    """
    Effectue les backtests pour differents timeframes, affiche les resultats,
    et identifie les meilleurs parametres pour le trading en temps reel.

    IMPORTANT: start_date sera recalcule dynamiquement a chaque appel pour toujours
    utiliser une fenetre glissante de 5 ans depuis aujourd'hui.
    """
    # Recalculer start_date dynamiquement a chaque execution (fenetre glissante 5 ans)
    dynamic_start_date = (datetime.today() - timedelta(days=deps.config.backtest_days)).strftime("%d %B %Y")

    console = Console()

    # DETECTION INTELLIGENTE DES CHANGEMENTS DE MARCHE
    console.print("\n[bold cyan][ANALYZE] Analyse des changements du marche...[/bold cyan]")
    market_changes = deps.detect_market_changes_fn(backtest_pair, deps.timeframes, dynamic_start_date)
    deps.display_market_changes_fn(market_changes, backtest_pair, console=console)

    logger.info(f"Backtest period: 5 years from today | Start date: {dynamic_start_date}")

    # COMPENSATION KRAKEN ULTRA-ROBUSTE A CHAQUE BACKTEST
    logger.info("Compensation timestamp Kraken ultra-robuste active")

    logger.info("Debut des backtests...")

    if backtest_pair not in deps.bot_state:
        with deps.bot_state_lock:
            if backtest_pair not in deps.bot_state:
                deps.bot_state[backtest_pair] = deps.make_default_pair_state_fn()

    pair_state = cast(Dict[str, Any], deps.bot_state[backtest_pair])

    try:
        results = deps.run_all_backtests_fn(backtest_pair, dynamic_start_date, deps.timeframes, sizing_mode=sizing_mode)
    except Exception as e:
        logger.error(f"Une erreur est survenue pendant les backtests : {e}")
        return

    if not results:
        logger.error("Aucune donnee de backtest n'a ete generee")
        return

    # === WALK-FORWARD VALIDATION — ML-07: Optuna bayésien (prioritaire) ===
    wf_result: Dict[str, Any] = {}
    try:
        from walk_forward import run_walk_forward_optuna
        # Recréer base_dataframes pour WF (données déjà en cache)
        wf_base_dataframes = {}
        for tf in deps.timeframes:
            df_wf = deps.prepare_base_dataframe_fn(backtest_pair, tf, dynamic_start_date, 14)
            wf_base_dataframes[tf] = df_wf if df_wf is not None and not df_wf.empty else __import__('pandas').DataFrame()

        # ML-07: Optuna en priorité (espace EMA + scenario continu)
        wf_result = run_walk_forward_optuna(
            base_dataframes=wf_base_dataframes,
            scenarios=deps.wf_scenarios,
            backtest_fn=deps.backtest_from_dataframe_fn,
            initial_capital=deps.config.initial_wallet,
            sizing_mode=sizing_mode,
            n_folds=getattr(deps.config, 'oos_min_folds', 4),
            n_trials=100,
            required_folds=getattr(deps.config, 'oos_min_folds', 4),
            progress_pair=backtest_pair,
            timeout_seconds=getattr(deps.config, 'wf_optuna_timeout_seconds', 1800),
            progress_trials=getattr(deps.config, 'wf_optuna_progress_trials', 10),
            progress_seconds=getattr(deps.config, 'wf_optuna_progress_seconds', 60),
        )

        if wf_result.get('any_passed'):
            console.print(Panel(
                f"[bold green]Walk-Forward Validation PASSED[/bold green]\n"
                f"Meilleure config WF: {wf_result.get('best_wf_config', {}).get('scenario', 'N/A')} "
                f"({wf_result.get('best_wf_config', {}).get('timeframe', 'N/A')})\n"
                f"OOS Sharpe moyen: {wf_result.get('best_wf_config', {}).get('avg_oos_sharpe', 0):.2f}",
                title="[bold cyan]Walk-Forward Validation[/bold cyan]",
                border_style="green", width=120
            ))
        else:
            console.print(Panel(
                "[bold yellow]Walk-Forward Validation: aucune config n'a passé les quality gates OOS[/bold yellow]\n"
                f"[dim]Protection seule: aucun nouvel achat tant qu'un snapshot WF {_wf_fold_label(deps.config)} n'est pas publié[/dim]",
                title="[bold cyan]Walk-Forward Validation[/bold cyan]",
                border_style="yellow", width=120
            ))
    except Exception as wf_err:
        logger.warning(f"[WF] Walk-forward validation skipped: {wf_err}")

    # Identifier le meilleur résultat — C-13 + P2-05: OOS quality gate centralisée
    _pool_main, _ = _apply_oos_quality_gate(
        results, backtest_pair, deps,
        log_tag="MAIN C-13",
    )

    # P2-01: utiliser la config Walk-Forward (OOS) en priorité → élimine le biais look-ahead.
    _wf_best_cfg = None
    try:
        _wf_best_cfg = wf_result.get('best_wf_config') if wf_result.get('any_passed') else None
    except Exception as _e:
        logger.warning("[WF] Impossible de récupérer best_wf_config: %s", _e)
        _wf_best_cfg = None

    if _wf_best_cfg:
        logger.info(
            "[MAIN P2-01] Sélection Walk-Forward OOS: %s EMA(%s,%s) %s — "
            "OOS Sharpe=%.2f (look-ahead éliminé).",
            _wf_best_cfg['scenario'],
            _wf_best_cfg['ema_periods'][0],
            _wf_best_cfg['ema_periods'][1],
            _wf_best_cfg['timeframe'],
            _wf_best_cfg.get('avg_oos_sharpe', 0.0),
        )
        logger.info(
            "[MAIN-WF] WF_VALIDATED %s — publication snapshot requise avant achats.",
            backtest_pair,
        )
        _snapshot = deps.build_snapshot_fn(backtest_pair, _wf_best_cfg, wf_base_dataframes)
        if deps.publish_snapshot_fn(_snapshot):
            best_params = _snapshot.as_best_params()
            with deps.bot_state_lock:
                pair_state['wf_status'] = 'validated'
                pair_state['wf_block_reason'] = None
                pair_state['wf_last_attempt_at'] = datetime.now(timezone.utc).isoformat()
                pair_state['entries_ready'] = True
                pair_state['wf_fallback'] = False
                deps.entries_ready[backtest_pair] = True
            deps.parity_backtest_fn(_snapshot, wf_base_dataframes, sizing_mode)
        else:
            best_params = dict(pair_state.get('last_best_params') or {})
        # S1: reset compteur fallback consécutifs
        with deps.bot_state_lock:
            pair_state['wf_fallback_consecutive'] = 0
    else:
        with deps.bot_state_lock:
            _mark_wf_blocked(
                pair_state,
                deps.entries_ready,
                backtest_pair,
                wf_result,
                log_tag='MAIN-WF',
            )
        # I2: Fallback dynamique — IS champion avec le plus de trades (moins risque d'overfit)
        # IMPORTANT: trades peut être un DataFrame — ne pas faire 'trades or []' (bool ambiguity)
        def _n_trades_fb(r: dict) -> int:
            t = r.get('trades')
            return len(t) if t is not None else 0
        _fb_candidates = sorted(_pool_main, key=_n_trades_fb, reverse=True)
        _fb = _fb_candidates[0] if _fb_candidates else None
        _n_fb = _n_trades_fb(_fb) if _fb is not None else 0
        if _fb is not None and _n_fb >= 5:
            best_params = {
                'timeframe': _fb['timeframe'],
                'ema1_period': _fb['ema_periods'][0],
                'ema2_period': _fb['ema_periods'][1],
                'scenario': _fb['scenario'],
            }
            best_params.update(deps.scenario_default_params.get(_fb['scenario'], {}))
            logger.warning(
                "[MAIN P1-WF] Fallback IS champion (max trades): %s %s EMA(%d/%d) "
                "— %d trades IS. Les achats restent bloqués par P0-03/oos_blocked.",
                _fb['scenario'], _fb['timeframe'],
                _fb['ema_periods'][0], _fb['ema_periods'][1],
                _n_fb,
            )
        else:
            best_params = {
                'timeframe': '1d',
                'ema1_period': 26,
                'ema2_period': 50,
                'scenario': 'StochRSI',
            }
            best_params.update(deps.scenario_default_params.get('StochRSI', {}))
            logger.warning(
                "[MAIN P1-WF] Aucun résultat IS valide — paramètres CONSERVATIFS par défaut "
                "(EMA 26/50, StochRSI, 1d). Les achats restent bloqués par P0-03/oos_blocked."
            )
        # S1: incrémenter compteur fallback consécutifs et alerter si persistant
        with deps.bot_state_lock:
            _fallback_count = pair_state.get('wf_fallback_consecutive', 0) + 1
            pair_state['wf_fallback_consecutive'] = _fallback_count
        if _fallback_count >= 3:
            logger.warning(
                "[REGIME-ALERT] %s en fallback WF depuis %d cycles consécutifs "
                "— régime adverse persistant ou données insuffisantes.",
                backtest_pair, _fallback_count,
            )

    # Afficher les resultats
    deps.display_backtest_table_fn(backtest_pair, results, console)

    # Mise a jour de l'etat du bot
    pair_state['last_best_params'] = best_params
    pair_state['execution_count'] = pair_state.get('execution_count', 0) + 1
    deps.save_fn()

    console.print("\n")
    logger.info("[MAIN] Analyse terminée; ordres réservés au worker Live.")

    # Gestion de l'historique d'execution
    current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    console.print(deps.build_tracking_panel_fn(pair_state, current_run_time))
    console.print("\n")
    # P4.2: mettre à jour last_run_time APRES le panel (fix "Temps écoulé 0:00:00")
    pair_state['last_run_time'] = current_run_time
