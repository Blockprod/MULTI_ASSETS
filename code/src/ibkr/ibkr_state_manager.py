"""
ibkr_state_manager.py — Persistance d'état pour le runtime IBKR Forex.

Reproduit la logique JSON_V1 + HMAC-SHA256 de state_manager.py
avec une clé IBKR_SECRET dédiée et des chemins isolés.

Aucune dépendance sur state_manager.py (qui importe bot_config et BINANCE_SECRET_KEY).
"""
from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import logging
import os
import threading
import time
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, Optional

logger = logging.getLogger("ibkr_forex")

# Format JSON signé — identique au système Binance
_JSON_HEADER = b"JSON_V1:"

# Throttle de sauvegarde : 5s minimum entre deux sauvegardes non-forcées
_SAVE_THROTTLE_SECONDS = 5.0
_last_save_time: float = 0.0
_save_lock = threading.Lock()


class IBKRStateError(Exception):
    """Erreur d'intégrité ou de persistance de l'état IBKR."""


class _StateEncoder(json.JSONEncoder):
    """Encode datetime, date et Decimal pour la sérialisation JSON."""

    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, date):
            return o.isoformat()
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def _get_hmac_key(ibkr_secret: str) -> bytes:
    return ibkr_secret.encode("utf-8")


def _compute_hmac(data: bytes, hmac_key: bytes) -> bytes:
    return hmac_mod.new(hmac_key, data, hashlib.sha256).digest()


def _state_path(states_dir: str, state_file: str) -> str:
    return os.path.join(states_dir, state_file)


def save_ibkr_state(
    bot_state: Dict,
    *,
    states_dir: str,
    state_file: str,
    ibkr_secret: str,
    force: bool = False,
) -> None:
    """Sauvegarde l'état IBKR sur disque au format JSON_V1 + HMAC-SHA256.

    Throttlé à 5s sauf si force=True.
    Écriture atomique via fichier .tmp + os.replace().

    Raises:
        IBKRStateError si la sauvegarde échoue.
    """
    global _last_save_time

    with _save_lock:
        now = time.time()
        if not force and (now - _last_save_time) < _SAVE_THROTTLE_SECONDS:
            return

        try:
            os.makedirs(states_dir, exist_ok=True)
            path = _state_path(states_dir, state_file)
            hmac_key = _get_hmac_key(ibkr_secret)

            state_bytes = json.dumps(
                bot_state, cls=_StateEncoder, ensure_ascii=False, indent=2
            ).encode("utf-8")

            # Vérification si état inchangé (évite les writes inutiles)
            if os.path.exists(path):
                try:
                    with open(path, "rb") as fh:
                        raw = fh.read()
                    if raw.startswith(_JSON_HEADER):
                        old_state_bytes = raw[len(_JSON_HEADER) + 32:]
                        if hash(old_state_bytes) == hash(state_bytes):
                            _last_save_time = now
                            return
                except Exception:
                    pass  # En cas de lecture impossible, on ré-écrit

            mac = _compute_hmac(state_bytes, hmac_key)
            signed_data = _JSON_HEADER + mac + state_bytes

            tmp_path = path + ".tmp"
            try:
                with open(tmp_path, "wb") as fh:
                    fh.write(signed_data)
                os.replace(tmp_path, path)
            except Exception:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                raise

            _last_save_time = now
            logger.debug("[IBKR-STATE] État sauvegardé → %s", path)

        except (OSError, TypeError, ValueError) as exc:
            raise IBKRStateError(f"Erreur sauvegarde état IBKR : {exc}") from exc
        except Exception as exc:
            logger.error("[IBKR-STATE] Erreur inattendue sauvegarde : %s", exc)
            raise IBKRStateError(f"Erreur inattendue sauvegarde état IBKR : {exc}") from exc


def load_ibkr_state(
    *,
    states_dir: str,
    state_file: str,
    ibkr_secret: str,
) -> Dict:
    """Charge l'état IBKR depuis le disque.

    Vérifie l'intégrité HMAC.
    Retourne {} si le fichier n'existe pas.

    Raises:
        IBKRStateError si le HMAC est invalide (fichier corrompu ou falsifié).
    """
    path = _state_path(states_dir, state_file)
    if not os.path.exists(path):
        logger.info("[IBKR-STATE] Aucun fichier d'état trouvé — démarrage avec état vide")
        return {}

    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise IBKRStateError(f"Impossible de lire le fichier d'état : {exc}") from exc

    if not raw.startswith(_JSON_HEADER):
        # Fichier plain JSON sans header (édition manuelle) — accepté avec warning
        try:
            stripped = raw.lstrip()
            if stripped and stripped[0:1] == b"{":
                logger.warning(
                    "[IBKR-STATE] Fichier d'état sans signature HMAC — "
                    "sera re-signé au prochain save"
                )
                return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise IBKRStateError(f"Format d'état non reconnu : {exc}") from exc
        raise IBKRStateError("Format de fichier d'état IBKR non reconnu")

    hmac_key = _get_hmac_key(ibkr_secret)
    mac_stored = raw[len(_JSON_HEADER): len(_JSON_HEADER) + 32]
    state_bytes = raw[len(_JSON_HEADER) + 32:]
    mac_computed = _compute_hmac(state_bytes, hmac_key)

    if not hmac_mod.compare_digest(mac_stored, mac_computed):
        raise IBKRStateError(
            "[IBKR-STATE] Intégrité compromise (HMAC invalide). "
            "Fichier d'état IBKR modifié ou corrompu."
        )

    loaded = json.loads(state_bytes.decode("utf-8"))
    logger.debug("[IBKR-STATE] État chargé depuis %s", path)
    return loaded


def write_heartbeat(states_dir: str, extra: Optional[Dict] = None) -> None:
    """Écrit un fichier heartbeat JSON (liveness pour le dashboard)."""
    path = os.path.join(states_dir, "heartbeat.json")
    data: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "bot": "IBKR_FOREX",
    }
    if extra:
        data.update(extra)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError as exc:
        logger.warning("[IBKR-STATE] Impossible d'écrire heartbeat : %s", exc)
