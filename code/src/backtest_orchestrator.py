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
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Tuple, cast

from rich.console import Console
from rich.panel import Panel
from exchange_client import ExchangePort

logger = logging.getLogger(__name__)


# ─── Injection de dépendances ─────────────────────────────────────────────────

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
    # Mutable shared state (same objects as MULTI_SYMBOLS globals — passed by reference)
    last_backtest_time: Dict[str, float]            # _last_backtest_time
    live_best_params: Dict[str, Dict[str, Any]]     # _live_best_params
    oos_alert_last_sent: Dict[str, float]           # _oos_alert_last_sent
    oos_alert_lock: Any                             # threading.Lock
    # Constants
    wf_scenarios: List[Dict[str, Any]]              # WF_SCENARIOS
    scenario_default_params: Dict[str, Dict[str, Any]]  # SCENARIO_DEFAULT_PARAMS


# ─── Fonctions extraites ──────────────────────────────────────────────────────

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
                    "[%s] Blocage P0-03 levé — %d/%d résultats passent les OOS gates.",
                    log_tag, len(valid), len(results),
                )
            else:
                logger.info(
                    "[%s] %d/%d résultats passent les OOS gates.",
                    log_tag, len(valid), len(results),
                )
        else:
            logger.info(
                "[%s] %d/%d résultats passent les OOS gates.",
                log_tag, len(valid), len(results),
            )
    else:
        pool = results
        blocked = True
        with deps.bot_state_lock:
            ps = deps.bot_state.setdefault(pair, {})
            ps['oos_blocked'] = True
            ps['oos_blocked_since'] = time.time()
        deps.save_fn(force=save_force)
        logger.critical(
            "[%s] Aucun résultat ne passe les OOS gates "
            "(Sharpe > %.1f & WR > %.0f%%) — ACHATS BLOQUÉS pour %s.",
            log_tag, deps.config.oos_sharpe_min, deps.config.oos_win_rate_min, pair,
        )
        if send_alert:
            # Cooldown: n'envoyer l'alerte qu'une fois par backtest_throttle_seconds (défaut 1h)
            _now_oos = time.time()
            _cooldown_oos = getattr(deps.config, 'backtest_throttle_seconds', 3600.0)
            with deps.oos_alert_lock:
                _last_sent = deps.oos_alert_last_sent.get(pair, 0.0)
            if (_now_oos - _last_sent) >= _cooldown_oos:
                try:
                    deps.send_alert_fn(
                        subject=f"[ALERTE {log_tag}] OOS gates non passées — achats bloqués {pair}",
                        body_main=(
                            f"Aucun résultat backtest ne passe les OOS gates pour {pair}.\n"
                            f"Critères: Sharpe > {deps.config.oos_sharpe_min} ET WinRate > {deps.config.oos_win_rate_min}%\n\n"
                            f"Les nouveaux achats sont bloqués. La gestion des stops reste active."
                        ),
                        client=deps.client,
                    )
                    with deps.oos_alert_lock:
                        deps.oos_alert_last_sent[pair] = _now_oos
                except Exception as _alert_err:
                    logger.error("[%s] Envoi alerte OOS impossible: %s", log_tag, _alert_err)
            else:
                logger.info(
                    "[%s] Alerte OOS throttled pour %s (cooldown %.0fs, reste %.0fs)",
                    log_tag, pair, _cooldown_oos, _cooldown_oos - (_now_oos - _last_sent),
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

        # Force flush de la console
        sys.stdout.flush()
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
                    sizing_mode=sizing_mode
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
                    log_tag="SCHEDULED C-13", send_alert=True,
                )

                # P2-01: Walk-Forward OOS validation pour la sélection planifiée.
                # ML-07: Optuna bayésien en priorité, fallback vers grid WF.
                _sched_wf_best = None
                try:
                    from walk_forward import run_walk_forward_optuna as _run_wf_optuna
                    from walk_forward import run_walk_forward_validation as _run_wf_sched
                    _wf_dfs_sched = {}
                    for _tf_s in deps.timeframes:
                        _df_s = deps.prepare_base_dataframe_fn(backtest_pair, _tf_s, dynamic_start_date, 14)
                        _wf_dfs_sched[_tf_s] = _df_s if _df_s is not None and not _df_s.empty else __import__('pandas').DataFrame()
                    # ML-07: Try Optuna first (wider EMA search space)
                    _wf_res_sched = _run_wf_optuna(
                        base_dataframes=_wf_dfs_sched,
                        scenarios=deps.wf_scenarios,
                        backtest_fn=deps.backtest_from_dataframe_fn,
                        initial_capital=deps.config.initial_wallet,
                        sizing_mode=sizing_mode,
                        n_trials=100,
                    )
                    # Fallback to grid WF if Optuna found nothing valid
                    if not _wf_res_sched.get('any_passed'):
                        logger.info("[SCHEDULED ML-07] Optuna WF: aucun config valide — fallback grid WF")
                        _wf_res_sched = _run_wf_sched(
                            base_dataframes=_wf_dfs_sched,
                            full_sample_results=backtest_results,
                            scenarios=deps.wf_scenarios,
                            backtest_fn=deps.backtest_from_dataframe_fn,
                            initial_capital=deps.config.initial_wallet,
                            sizing_mode=sizing_mode,
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
                    else:
                        logger.info("[SCHEDULED P2-01] Aucun résultat WF valide — fallback IS-Calmar.")
                except Exception as _wf_sched_err:
                    logger.warning("[SCHEDULED P2-01] WF validation skipped: %s", _wf_sched_err)

                best_result = deps.select_best_by_calmar_fn(_selection_pool)
                best_profit = best_result['final_wallet'] - best_result['initial_wallet']

                logger.info(f"[SCHEDULED] Meilleur resultat IS: {best_result['scenario']} sur {best_result['timeframe']} | Profit IS: ${best_profit:,.2f}")

                # === AFFICHAGE DES RESULTATS ===
                try:
                    deps.display_results_fn(backtest_pair, backtest_results)
                    logger.info(f"[SCHEDULED] Résultats affichés pour {backtest_pair}")
                    sys.stdout.flush()
                except Exception as display_err:
                    logger.error(f"[SCHEDULED] Erreur affichage résultats: {str(display_err)}")

                # P2-01: utiliser WF OOS config si disponible, sinon IS-Calmar
                if _sched_wf_best:
                    updated_best_params = {
                        'timeframe': _sched_wf_best['timeframe'],
                        'ema1_period': _sched_wf_best['ema_periods'][0],
                        'ema2_period': _sched_wf_best['ema_periods'][1],
                        'scenario': _sched_wf_best['scenario'],
                    }
                else:
                    updated_best_params = {
                        'timeframe': best_result['timeframe'],
                        'ema1_period': best_result['ema_periods'][0],
                        'ema2_period': best_result['ema_periods'][1],
                        'scenario': best_result['scenario'],
                    }
                updated_best_params.update(deps.scenario_default_params.get(updated_best_params.get('scenario', 'StochRSI'), {}))

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

        # Exécuter le trading avec les paramètres mis à jour
        try:
            logger.info(f"[SCHEDULED] Appel execute_real_trades avec {best_params['scenario']} sur {best_params['timeframe']} + sizing_mode='{sizing_mode}'...")
            deps.execute_trades_fn(real_trading_pair, best_params['timeframe'], best_params, backtest_pair, sizing_mode=sizing_mode)
            logger.info("[SCHEDULED] execute_real_trades complété avec succès")
        except Exception as trade_error:
            logger.error(f"[SCHEDULED] Erreur dans execute_real_trades: {str(trade_error)}")
            logger.error(f"[SCHEDULED] Traceback: {traceback.format_exc()}")
            try:
                deps.send_alert_fn(
                    subject=f"[ALERTE P1] Erreur execute_real_trades — {backtest_pair}",
                    body_main=(
                        f"Une exception non gérée s'est produite dans execute_real_trades.\n\n"
                        f"Paire : {backtest_pair}\n"
                        f"Erreur : {type(trade_error).__name__}: {str(trade_error)[:300]}\n\n"
                        f"Traceback (tronqué) :\n{traceback.format_exc()[:500]}"
                    ),
                    client=deps.client,
                )
            except Exception as _e:
                logger.warning("[SCHEDULED] Email alerte trade impossible: %s", _e)

        # === AFFICHAGE PANEL - SUIVI & PLANIFICATION ===
        logger.info("[SCHEDULED] Affichage des informations de suivi...")

        # Assurer l'initialisation par defaut de l'etat de la paire
        pair_state = cast(Dict[str, Any], deps.bot_state.setdefault(backtest_pair, {}))
        # IMPORTANT : Ne pas réinitialiser last_order_side s'il existe déjà
        if 'last_order_side' not in pair_state:
            pair_state['last_order_side'] = None
        current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Mettre à jour l'état
        pair_state['last_run_time'] = current_run_time
        deps.save_fn()

        # Persister les params actifs pour que la lambda les lise au prochain cycle.
        with deps.bot_state_lock:
            deps.live_best_params[backtest_pair] = dict(best_params)

        # Afficher le panel de suivi
        logger.info("[SCHEDULED] Création et affichage du panel de suivi...")
        try:
            deps.console.print(deps.build_tracking_panel_fn(pair_state, current_run_time))
            deps.console.print("\n")
            sys.stdout.flush()
            logger.info(f"[SCHEDULED] Exécution planifiée COMPLETEE pour {backtest_pair}")
        except Exception as tracking_err:
            logger.error(f"[SCHEDULED] Erreur affichage tracking panel: {str(tracking_err)}")

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
        if not current_params or 'timeframe' not in current_params:
            logger.warning(f"[LIVE-ONLY] {backtest_pair}: paramètres non disponibles, skip.")
            return

        tf = current_params['timeframe']
        logger.info(
            "[LIVE-ONLY] %s @ %s — %s EMA(%s/%s) %s",
            backtest_pair,
            datetime.now().strftime('%H:%M:%S'),
            current_params.get('scenario'),
            current_params.get('ema1_period'),
            current_params.get('ema2_period'),
            tf,
        )

        deps.execute_trades_fn(real_trading_pair, tf, current_params, backtest_pair, sizing_mode=sizing_mode)

        pair_state = cast(Dict[str, Any], deps.bot_state.setdefault(backtest_pair, {}))
        current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pair_state['last_run_time'] = current_run_time
        pair_state['last_execution'] = current_run_time
        deps.save_fn()

        try:
            deps.console.print(deps.build_tracking_panel_fn(pair_state, current_run_time))
            deps.console.print("\n")
            sys.stdout.flush()
        except Exception as _panel_err:
            logger.error(f"[LIVE-ONLY] Erreur panel tracking: {_panel_err}")

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

    # COMPENSATION BINANCE ULTRA-ROBUSTE A CHAQUE BACKTEST
    logger.info("Compensation timestamp Binance ultra-robuste active")

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
        from walk_forward import run_walk_forward_optuna, run_walk_forward_validation
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
            n_trials=100,
        )
        # Fallback: grid WF si Optuna ne passe pas les OOS gates
        if not wf_result.get('any_passed'):
            logger.info("[ML-07] Optuna WF: aucun config valide — fallback grid WF")
            wf_result = run_walk_forward_validation(
                base_dataframes=wf_base_dataframes,
                full_sample_results=results,
                scenarios=deps.wf_scenarios,
                backtest_fn=deps.backtest_from_dataframe_fn,
                initial_capital=deps.config.initial_wallet,
                sizing_mode=sizing_mode,
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
                "[dim]Utilisation du meilleur résultat full-sample (mode dégradé)[/dim]",
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
        best_params = {
            'timeframe': _wf_best_cfg['timeframe'],
            'ema1_period': _wf_best_cfg['ema_periods'][0],
            'ema2_period': _wf_best_cfg['ema_periods'][1],
            'scenario': _wf_best_cfg['scenario'],
        }
        best_params.update(deps.scenario_default_params.get(_wf_best_cfg['scenario'], {}))
    else:
        best_params = {
            'timeframe': '1d',  # conservative default
            'ema1_period': 26,
            'ema2_period': 50,
            'scenario': 'StochRSI',
        }
        best_params.update(deps.scenario_default_params.get('StochRSI', {}))
        logger.warning(
            "[MAIN P1-WF] Aucun résultat WF valide — paramètres CONSERVATIFS par défaut "
            "(EMA 26/50, StochRSI, 1d). Les achats restent bloqués par P0-03/oos_blocked."
        )

    # Afficher les resultats
    deps.display_backtest_table_fn(backtest_pair, results, console)

    # Execution des ordres reels avec les meilleurs parametres

    # Mise a jour de l'etat du bot
    pair_state['last_best_params'] = best_params
    pair_state['execution_count'] = pair_state.get('execution_count', 0) + 1
    deps.save_fn()

    console.print("\n")
    try:
        # POSITION SIZING: utiliser le mode passé en paramètre
        deps.execute_trades_fn(real_trading_pair, best_params['timeframe'], best_params, backtest_pair, sizing_mode=sizing_mode)
    except Exception as e:
        logger.error(f"Une erreur est survenue lors de l'execution des ordres en reel: {e}")
        from email_templates import trading_execution_error_email
        subj, body = trading_execution_error_email(str(e), traceback.format_exc())
        deps.send_email_alert_fn(subject=subj, body=body)

    # Gestion de l'historique d'execution
    current_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Panel pour l'historique et la planification
    pair_state['last_run_time'] = current_run_time

    # SOLUTION DEFINITIVE - Éviter les planifications multiples
    existing_job = None
    for job in deps.schedule.jobs:
        try:
            job_func = job.job_func
            if job_func is not None and hasattr(job_func, 'args') and len(job_func.args) >= 2 and job_func.args[0] == backtest_pair:
                existing_job = job
                break
        except Exception:
            continue

    if existing_job:
        deps.schedule.cancel_job(existing_job)
        logger.info(f"Ancienne planification supprimée pour {backtest_pair}")

    # Programmer une tâche UNIQUE et HOMOGENE toutes les 2 minutes
    deps.schedule.every(deps.config.schedule_interval_minutes).minutes.do(
        lambda bp=backtest_pair, rp=real_trading_pair, tfs=deps.timeframes, sm=sizing_mode, d=deps:
            _backtest_and_display_results(
                bp,
                rp,
                (datetime.today() - timedelta(days=d.config.backtest_days)).strftime("%d %B %Y"),
                tfs,
                sm,
                d,
            )
    )

    console.print(deps.build_tracking_panel_fn(pair_state, current_run_time))
    console.print("\n")
