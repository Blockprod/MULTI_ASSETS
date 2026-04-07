"""metrics.py — Observabilité du bot : écriture périodique de métriques (P2-04).

Exporte un snapshot de l'état courant du bot sous forme de fichier JSON
``metrics/metrics.json`` (créé automatiquement si le dossier n'existe pas).

Ce fichier peut être lu par n'importe quel dashboard externe (Grafana, cron,
script de supervision) sans exposer de port réseau.

Public API
----------
- ``write_metrics(bot_state, runtime, circuit_breaker)``
- ``MetricsSnapshot`` (TypedDict — structure du JSON exporté)

Format du fichier
-----------------
::

    {
        "timestamp_utc": "2026-04-06T10:30:00Z",
        "bot_version": "P2-04",
        "emergency_halt": false,
        "save_failure_count": 0,
        "taker_fee": 0.0007,
        "pairs": {
            "BTCUSDC": {
                "in_position": true,
                "entry_price": 62000.0,
                "oos_blocked": false,
                "drawdown_halted": false,
                "sl_placed": true,
                "last_execution": "2026-04-06T10:25:00Z",
                "execution_count": 42
            },
            ...
        },
        "api_latency_ms": null
    }
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Chemin du fichier metrics (relatif à la racine du projet)
_METRICS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', 'metrics')
)
_METRICS_FILE = os.path.join(_METRICS_DIR, 'metrics.json')


def write_metrics(
    bot_state: Dict[str, Any],
    runtime: Any,
    circuit_breaker: Any = None,
    pairs: Optional[list] = None,
    api_latency_ms: Optional[float] = None,
) -> bool:
    """Écrit un snapshot des métriques du bot dans ``metrics/metrics.json``.

    Parameters
    ----------
    bot_state : dict
        Le dict global ``bot_state`` de MULTI_SYMBOLS.
    runtime : _BotRuntime
        L'objet singleton ``_runtime`` de MULTI_SYMBOLS.
    circuit_breaker : object, optional
        Instance du circuit breaker (``error_handler.circuit_breaker``).
        Utilisé pour lire ``is_available()``.
    pairs : list of str, optional
        Liste des paires actives (e.g. ``['BTCUSDC', 'ETHUSDC']``).
        Si None, toutes les clés non-système de ``bot_state`` sont utilisées.
    api_latency_ms : float, optional
        Latence de la dernière requête API en millisecondes.

    Returns
    -------
    bool
        True si l'écriture a réussi, False sinon.
    """
    try:
        # --- Sélectionner les paires ---
        _system_keys = {'emergency_halt', 'emergency_halt_reason', '_daily_pnl_tracker',
                        '_state_version', 'reconcile_failed'}
        if pairs is None:
            pairs = [k for k in bot_state.keys() if k not in _system_keys]

        # --- Construire le snapshot paire par paire ---
        pairs_snapshot: Dict[str, Any] = {}
        for pair in pairs:
            ps = bot_state.get(pair)
            if not isinstance(ps, dict):
                continue
            pairs_snapshot[pair] = {
                'in_position': ps.get('last_order_side') == 'BUY',
                'entry_price': ps.get('entry_price'),
                'oos_blocked': bool(ps.get('oos_blocked', False)),
                'drawdown_halted': bool(ps.get('drawdown_halted', False)),
                'sl_placed': bool(ps.get('sl_exchange_placed', False)),
                'last_execution': ps.get('last_execution'),
                'execution_count': ps.get('execution_count', 0),
            }

        # --- Circuit breaker ---
        cb_available: Optional[bool] = None
        if circuit_breaker is not None:
            try:
                cb_available = bool(circuit_breaker.is_available())
            except Exception as _exc:
                logger.debug("[metrics] circuit_breaker.is_available() a échoué: %s", _exc)

        # --- Snapshot global ---
        snapshot: Dict[str, Any] = {
            'timestamp_utc': datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'bot_version': 'P2-04',
            'emergency_halt': bool(bot_state.get('emergency_halt', False)),
            'emergency_halt_reason': bot_state.get('emergency_halt_reason'),
            'save_failure_count': getattr(runtime, 'save_failure_count', 0),
            'taker_fee': getattr(runtime, 'taker_fee', None),
            'maker_fee': getattr(runtime, 'maker_fee', None),
            'circuit_breaker_available': cb_available,
            'pairs': pairs_snapshot,
            'api_latency_ms': api_latency_ms,
        }

        # --- Écriture atomique (write-then-rename) ---
        os.makedirs(_METRICS_DIR, exist_ok=True)
        tmp_path = _METRICS_FILE + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as fh:
            json.dump(snapshot, fh, indent=2, ensure_ascii=False)
        os.replace(tmp_path, _METRICS_FILE)

        logger.debug("[METRICS] Snapshot écrit: %s paires, emergency_halt=%s",
                     len(pairs_snapshot), snapshot['emergency_halt'])
        return True

    except Exception as exc:
        logger.warning("[METRICS] Échec écriture metrics.json: %s", exc)
        return False


def read_metrics() -> Optional[Dict[str, Any]]:
    """Lit le dernier snapshot de métriques depuis ``metrics/metrics.json``.

    Retourne None si le fichier n'existe pas ou est invalide.
    Principalement utile pour les tests et les scripts de vérification.
    """
    if not os.path.exists(_METRICS_FILE):
        return None
    try:
        with open(_METRICS_FILE, 'r', encoding='utf-8') as fh:
            data: Dict[str, Any] = json.load(fh)
        return data
    except Exception as exc:
        logger.warning("[METRICS] Échec lecture metrics.json: %s", exc)
        return None
