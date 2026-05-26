"""
ibkr_wal.py — Write-Ahead Log pour la chaîne BUY→SL IBKR Forex.

Protège contre les crashes entre l'exécution du BUY et le placement du SL.
Au redémarrage, ibkr_wal_replay() détecte les opérations incomplètes :
  - FX_BUY_CONFIRMED sans FX_SL_PLACED → re-placement du SL ou sell d'urgence.

Format JSON-lines (un dict JSON par ligne) dans states/ibkr_wal.jsonl.
fsync à chaque écriture — garantie durabilité sur crash process.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("ibkr_forex")

# C3: chemin absolu basé sur __file__ — évite le risque de création
# dans le répertoire courant si CWD ≠ racine projet (ex: scheduler Windows).
_WAL_FILE = Path(__file__).parent / "states" / "ibkr_wal.jsonl"
_WAL_LOCK = threading.Lock()

# ─── Codes d'opération ────────────────────────────────────────────────────────
OP_FX_BUY_INTENT = "FX_BUY_INTENT"       # avant appel safe_forex_buy
OP_FX_BUY_CONFIRMED = "FX_BUY_CONFIRMED" # après fill confirmé
OP_FX_SL_PLACED = "FX_SL_PLACED"         # après SL posé sur exchange


def ibkr_wal_write(op: str, payload: Dict[str, Any]) -> None:
    """Ajoute une entrée au WAL IBKR. Thread-safe. fsync à chaque appel."""
    import time as _t
    entry = {"op": op, "ts": _t.time(), **payload}
    _WAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _WAL_LOCK:
        with open(_WAL_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    logger.debug("[IBKR-WAL] %s pair=%s", op, payload.get("pair", "?"))


def ibkr_wal_clear(pair: str) -> None:
    """Supprime toutes les entrées WAL pour une paire (opération complète)."""
    with _WAL_LOCK:
        if not _WAL_FILE.exists():
            return
        lines = _WAL_FILE.read_text(encoding="utf-8").splitlines()
        kept = [ln for ln in lines if _safe_pair(ln) != pair]
        _WAL_FILE.write_text(
            "\n".join(kept) + ("\n" if kept else ""),
            encoding="utf-8",
        )
    logger.debug("[IBKR-WAL] WAL nettoyé pour %s", pair)


def ibkr_wal_replay() -> List[Dict[str, Any]]:
    """Retourne les opérations incomplètes pour le replay au démarrage.

    Une opération est incomplète si FX_BUY_CONFIRMED existe sans FX_SL_PLACED
    pour la même paire.

    Returns
    -------
    Liste de dicts (dernière entrée par paire) pour chaque paire incomplète.
    """
    if not _WAL_FILE.exists():
        return []

    with _WAL_LOCK:
        raw = _WAL_FILE.read_text(encoding="utf-8").splitlines()

    by_pair: Dict[str, List[Dict[str, Any]]] = {}
    for line in raw:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            pair = entry.get("pair", "UNKNOWN")
            by_pair.setdefault(pair, []).append(entry)
        except json.JSONDecodeError:
            logger.warning("[IBKR-WAL] Entrée invalide ignorée: %.80s", line)

    incomplete: List[Dict[str, Any]] = []
    for pair, entries in by_pair.items():
        ops = {e["op"] for e in entries}
        if OP_FX_BUY_CONFIRMED in ops and OP_FX_SL_PLACED not in ops:
            last = max(entries, key=lambda e: e.get("ts", 0.0))
            incomplete.append(last)
            logger.warning(
                "[IBKR-WAL] Opération incomplète détectée — pair=%s ops=%s",
                pair, ops,
            )

    return incomplete


def _safe_pair(line: str) -> str:
    """Extrait le champ 'pair' d'une ligne JSON sans lever d'exception."""
    try:
        return json.loads(line).get("pair", "")
    except json.JSONDecodeError:
        return ""
