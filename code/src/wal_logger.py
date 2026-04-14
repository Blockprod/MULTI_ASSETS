"""wal_logger.py — Write-Ahead Log (WAL) minimaliste pour les opérations critiques.

Objectif (B-05) :
  Un crash entre un BUY exécuté et le save_bot_state() suivant peut faire perdre
  entry_price / atr_at_entry / stop_loss_at_entry.  Le WAL écrit un intent record
  *avant* l'appel Binance et un confirmed record *après* fill.  Au redémarrage,
  wal_replay() détecte les intents sans confirmation et renvoie les paires
  concernées pour une réconciliation immédiate.

Format fichier : JSONL append-only, une opération par ligne.
  {"ts": 1713109680, "op": "BUY_INTENT",    "pair": "BTCUSDC", "qty_str": "0.001", ...}
  {"ts": 1713109682, "op": "BUY_CONFIRMED", "pair": "BTCUSDC", "order_id": "abc123"}
  {"ts": 1713109684, "op": "SL_PLACED",     "pair": "BTCUSDC", "sl_order_id": "def456"}

Thread-safety :
  _wal_lock (threading.Lock) dédié, distinct de _bot_state_lock.  Les écritures
  sont atomiques (ligne complète + flush + fsync).

Durabilité :
  Le fichier est dans states/wal.jsonl (même répertoire que bot_state.json).
  wal_clear(pair) supprime les entrées de la paire après réconciliation réussie.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_WAL_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'states')
_WAL_FILE = os.path.join(_WAL_DIR, 'wal.jsonl')

# Opérations valides
OP_BUY_INTENT = "BUY_INTENT"
OP_BUY_CONFIRMED = "BUY_CONFIRMED"
OP_SL_PLACED = "SL_PLACED"

# Intents qui nécessitent une confirmation — si absente au replay → réconciliation
_INTENT_OPS: Set[str] = {OP_BUY_INTENT}
_CONFIRM_OPS: Set[str] = {OP_BUY_CONFIRMED, OP_SL_PLACED}

_wal_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------

def wal_write(op: str, pair: str, **kwargs: Any) -> None:
    """Écrit un record WAL atomiquement.

    Ne lève jamais : toute exception est loggée en warning (le WAL est un filet
    de sécurité, pas un chemin critique — ne jamais crasher le bot à cause de lui).
    """
    record: Dict[str, Any] = {"ts": time.time(), "op": op, "pair": pair}
    record.update(kwargs)
    line = json.dumps(record, separators=(',', ':'))
    try:
        with _wal_lock:
            os.makedirs(_WAL_DIR, exist_ok=True)
            with open(_WAL_FILE, 'a', encoding='utf-8') as fh:
                fh.write(line + '\n')
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass  # fsync best-effort (Windows sometimes raises)
    except Exception as exc:
        logger.warning("[WAL] Impossible d'écrire le record %s/%s: %s", op, pair, exc)


# ---------------------------------------------------------------------------
# Replay au démarrage
# ---------------------------------------------------------------------------

def wal_replay() -> List[str]:
    """Analyse le WAL et retourne les paires avec des intents sans confirmation.

    Logique :
      - Lire toutes les lignes du WAL.
      - Pour chaque paire, si BUY_INTENT est présent mais BUY_CONFIRMED absent
        (ou si BUY_CONFIRMED est présent mais SL_PLACED absent), la paire est
        considérée comme nécessitant une réconciliation.

    Returns :
      Liste de paires (ex. ["BTCUSDC", "ETHUSDC"]) à réconcilier immédiatement.
      Retourne [] si le WAL est absent ou vide.
    """
    if not os.path.exists(_WAL_FILE):
        return []

    records: List[Dict[str, Any]] = []
    try:
        with _wal_lock:
            with open(_WAL_FILE, 'r', encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning("[WAL] Ligne WAL corrompue ignorée: %s — %s", line[:80], e)
    except Exception as exc:
        logger.warning("[WAL] Impossible de lire le WAL pour replay: %s", exc)
        return []

    # Grouper par paire, dans l'ordre chronologique
    by_pair: Dict[str, List[str]] = {}
    for rec in sorted(records, key=lambda r: r.get('ts', 0)):
        p = rec.get('pair', '')
        if p:
            by_pair.setdefault(p, []).append(rec.get('op', ''))

    unconfirmed: List[str] = []
    for pair, ops in by_pair.items():
        has_intent = OP_BUY_INTENT in ops
        has_confirmed = OP_BUY_CONFIRMED in ops
        if has_intent and not has_confirmed:
            logger.warning(
                "[WAL] Intent BUY sans confirmation détecté pour %s → réconciliation requise", pair
            )
            unconfirmed.append(pair)
        elif has_confirmed and OP_SL_PLACED not in ops:
            logger.warning(
                "[WAL] BUY confirmé mais SL_PLACED absent pour %s → vérification requise", pair
            )
            unconfirmed.append(pair)

    if unconfirmed:
        logger.warning("[WAL] %d paire(s) avec intent non confirmé: %s", len(unconfirmed), unconfirmed)
    else:
        logger.info("[WAL] Replay OK — aucun intent non confirmé détecté")

    return unconfirmed


# ---------------------------------------------------------------------------
# Nettoyage après réconciliation réussie
# ---------------------------------------------------------------------------

def wal_clear(pair: Optional[str] = None) -> None:
    """Supprime les entrées WAL d'une paire (ou tout le WAL si pair=None).

    Appelé après réconciliation réussie pour éviter que les intents soient
    rejoués au prochain redémarrage.
    """
    if not os.path.exists(_WAL_FILE):
        return
    try:
        with _wal_lock:
            if pair is None:
                # Tout effacer
                open(_WAL_FILE, 'w', encoding='utf-8').close()
                logger.info("[WAL] WAL entièrement vidé")
                return

            # Filtrer les lignes de la paire
            remaining: List[str] = []
            with open(_WAL_FILE, 'r', encoding='utf-8') as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        rec = json.loads(stripped)
                        if rec.get('pair') != pair:
                            remaining.append(stripped)
                    except json.JSONDecodeError:
                        remaining.append(stripped)  # conserver les lignes illisibles

            with open(_WAL_FILE, 'w', encoding='utf-8') as fh:
                for line in remaining:
                    fh.write(line + '\n')
                fh.flush()

            logger.info("[WAL] Entrées WAL pour %s supprimées (%d lignes restantes)", pair, len(remaining))
    except Exception as exc:
        logger.warning("[WAL] Impossible de nettoyer le WAL pour %s: %s", pair, exc)
