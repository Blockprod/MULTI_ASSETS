"""P1-01: Cython .pyd integrity verification at boot.

Calcule le SHA256 des modules Cython compilés (.pyd) et compare aux
checksums enregistrés dans code/bin/checksums.json.

En cas de mismatch : log CRITICAL + email d'alerte + fallback Python explicite.
Exporte le flag CYTHON_INTEGRITY_VERIFIED consultable par le watchdog.

Utilisation au boot :
    from cython_integrity import verify_cython_integrity, CYTHON_INTEGRITY_VERIFIED
    verify_cython_integrity(alert_fn=send_trading_alert_email)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

_BIN_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin'))
_CHECKSUMS_FILE = os.path.join(_BIN_DIR, 'checksums.json')

# Flag consultable après appel à verify_cython_integrity()
CYTHON_INTEGRITY_VERIFIED: bool = False


def _sha256_file(path: str) -> str:
    """Calcule le SHA256 d'un fichier binaire (chunked pour gros .pyd)."""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _load_checksums() -> Optional[Dict[str, str]]:
    """Charge checksums.json, retourne None si absent ou illisible."""
    if not os.path.exists(_CHECKSUMS_FILE):
        logger.warning(
            "[CYTHON-INTEGRITY] checksums.json absent (%s) — vérification ignorée",
            _CHECKSUMS_FILE,
        )
        return None
    try:
        with open(_CHECKSUMS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning("[CYTHON-INTEGRITY] Impossible de lire checksums.json: %s", e)
        return None


def verify_cython_integrity(
    alert_fn: Optional[Callable[[str, str], None]] = None,
) -> bool:
    """Vérifie les SHA256 des .pyd contre checksums.json.

    alert_fn signature attendue : alert_fn(subject: str, body: str)
    Compatible avec send_email_alert de email_utils.

    Retourne True si tout est OK, False en cas de mismatch ou si
    checksums.json est absent.

    Positionne le flag module-level CYTHON_INTEGRITY_VERIFIED.
    """
    global CYTHON_INTEGRITY_VERIFIED

    expected = _load_checksums()
    if expected is None:
        CYTHON_INTEGRITY_VERIFIED = False
        return False

    failed: list[str] = []
    for filename, expected_hash in expected.items():
        pyd_path = os.path.join(_BIN_DIR, filename)
        if not os.path.exists(pyd_path):
            # Fichier peut ne pas exister sur cette version Python — skip silencieux
            logger.debug(
                "[CYTHON-INTEGRITY] %s absent sur ce runtime — ignoré", filename
            )
            continue
        actual_hash = _sha256_file(pyd_path)
        if actual_hash != expected_hash:
            failed.append(filename)
            logger.critical(
                "[CYTHON-INTEGRITY] CHECKSUM MISMATCH: %s\n"
                "  attendu : %s...\n"
                "  réel    : %s...",
                filename,
                expected_hash[:16],
                actual_hash[:16],
            )

    if failed:
        CYTHON_INTEGRITY_VERIFIED = False
        if alert_fn is not None:
            try:
                alert_fn(
                    f"[CRITIQUE P1-01] Cython .pyd corrompu "
                    f"({len(failed)} fichier(s)) — fallback Python actif",
                    "Les modules Cython suivants ont un checksum invalide :\n\n"
                    + "\n".join(f"  - {f}" for f in failed)
                    + "\n\nLe bot continue en mode Python fallback (50x plus lent)."
                    "\nIntervention manuelle requise.",
                )
            except Exception as e:
                logger.warning(
                    "[CYTHON-INTEGRITY] Email alerte impossible: %s", e
                )
        return False

    logger.info(
        "[CYTHON-INTEGRITY] Checksums OK — %d module(s) Cython vérifié(s)",
        len(expected),
    )
    CYTHON_INTEGRITY_VERIFIED = True
    return True


def generate_checksums() -> Dict[str, str]:
    """Génère checksums.json depuis les .pyd actuels. À lancer au build time.

    Exemple :
        python -c "from cython_integrity import generate_checksums; generate_checksums()"
    """
    checksums: Dict[str, str] = {}
    for fname in sorted(os.listdir(_BIN_DIR)):
        if fname.endswith('.pyd'):
            path = os.path.join(_BIN_DIR, fname)
            checksums[fname] = _sha256_file(path)
            logger.info(
                "[CYTHON-INTEGRITY] %s → %s...", fname, checksums[fname][:16]
            )
    with open(_CHECKSUMS_FILE, 'w', encoding='utf-8') as f:
        json.dump(checksums, f, indent=2)
    logger.info(
        "[CYTHON-INTEGRITY] checksums.json écrit (%d fichier(s))", len(checksums)
    )
    return checksums
