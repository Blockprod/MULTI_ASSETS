"""
timestamp_utils.py — Time synchronization & network connectivity utilities.

Extracted from MULTI_SYMBOLS.py (P3-SRP) to isolate OS-level operations
(Windows clock sync, admin checks, DNS connectivity) from trading logic.

All functions accept explicit parameters (no globals).
"""

from __future__ import annotations

import ctypes
import logging
import os
import socket
import subprocess
import time
from typing import Any, Callable, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Admin / Privilege Checks ────────────────────────────────────────────────

def check_admin_privileges() -> bool:
    """Vérifie les privilèges admin de manière cross-platform.

    Returns
    -------
    bool
        ``True`` if the current process runs with administrator / root
        privileges, ``False`` otherwise.

    Notes
    -----
    - Windows: calls ``IsUserAnAdmin`` via *ctypes*.
    - Linux / macOS: checks ``os.getuid() == 0``.
    """
    if os.name == 'nt':
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    else:
        # Linux / macOS : root = uid 0
        try:
            return getattr(os, 'getuid', lambda: -1)() == 0
        except AttributeError:
            return False


# ─── Windows Time Sync ───────────────────────────────────────────────────────

def sync_windows_silently() -> bool:
    """Synchronise l'horloge Windows via *w32tm* si privilèges admin disponibles.

    Exécute silencieusement la séquence ``net stop/start w32time``,
    ``w32tm /config``, ``w32tm /resync /force``.

    Returns
    -------
    bool
        ``True`` si ≥ 3 commandes sur 5 ont réussi, ``False`` sinon
        (y compris sur OS non-Windows).
    """
    if os.name != 'nt':
        logger.debug("sync_windows_silently: ignoré (OS non-Windows)")
        return False

    try:
        # Vérifier les privilèges admin
        if not check_admin_privileges():
            logger.debug("Pas de privilèges admin - synchronisation ignorée")
            return False

        logger.info("Synchronisation Windows silencieuse en cours...")

        # Commandes de synchronisation directes (sans popup)
        commands = [
            ['net', 'stop', 'w32time'],
            ['net', 'start', 'w32time'],
            ['w32tm', '/config', '/manualpeerlist:time.windows.com,0x1',
             '/syncfromflags:manual', '/reliable:yes'],
            ['w32tm', '/config', '/update'],
            ['w32tm', '/resync', '/force'],
        ]

        success_count = 0
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    timeout=15,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode == 0:
                    success_count += 1
                    logger.debug(f"Commande réussie: {' '.join(cmd)}")
            except Exception as e:
                logger.debug(f"Erreur commande {' '.join(cmd)}: {e}")
                continue

        if success_count >= 3:
            logger.info("Synchronisation Windows silencieuse: REUSSIE")
            return True
        else:
            logger.warning(f"Synchronisation partielle: {success_count}/5")
            return False

    except Exception as e:
        logger.debug(f"Erreur synchronisation silencieuse: {e}")
        return False


def init_timestamp_solution(client: Any) -> bool:
    """Initialisation robuste de la synchronisation horloge ↔ Binance.

    Appelle successivement ``_perform_ultra_robust_sync``,
    ``_sync_server_time_robust`` ou ``_sync_server_time`` selon les
    méthodes disponibles sur *client*, puis valide le delta.

    Parameters
    ----------
    client : BinanceFinalClient
        Instance du client Binance disposant des méthodes de sync.

    Returns
    -------
    bool
        ``True`` si la synchronisation a réussi, ``False`` en cas d'échec.
    """
    try:
        logger.info("=== INITIALISATION SYNCHRO ULTRA ROBUSTE ===")

        # Utiliser la BONNE méthode
        if hasattr(client, '_perform_ultra_robust_sync'):
            client._perform_ultra_robust_sync()
        elif hasattr(client, '_sync_server_time_robust'):
            client._sync_server_time_robust()
        else:
            client._sync_server_time()

        # Test de validation
        if hasattr(client, '_get_ultra_safe_timestamp'):
            test_ts = client._get_ultra_safe_timestamp()
        else:
            test_ts = client._get_synchronized_timestamp()

        server_ts = client.get_server_time()['serverTime']
        diff = test_ts - server_ts

        logger.info(f"VALIDATION: diff={diff}ms")

        if abs(diff) < 1000:
            logger.info("SYNCHRONISATION PARFAITE - Prêt pour le trading")
        else:
            logger.info(f"SYNCHRONISATION STABLE: {diff}ms")

        return True

    except Exception as e:
        logger.error(f"Échec initialisation: {e}")
        logger.info("Fallback robuste activé")
        return False


# ─── Network Connectivity ────────────────────────────────────────────────────

def check_network_connectivity() -> bool:
    """Vérifie la connectivité réseau et tente de la rétablir.

    Effectue un ``socket.create_connection`` vers 8.8.8.8:53.  En cas
    d'échec sur Windows, tente un flush DNS + renouvellement DHCP.

    Returns
    -------
    bool
        ``True`` si la connectivité est (re)établie, ``False`` sinon.
    """
    try:
        # Test de connectivité basique
        socket.create_connection(("8.8.8.8", 53), timeout=5)
        return True
    except (socket.error, OSError):
        logger.warning("Perte de connectivité réseau détectée")

        if os.name != 'nt':
            logger.warning("Réactivation réseau automatique non supportée sur cet OS")
            return False

        try:
            # Tenter de réactiver la connexion réseau (Windows uniquement)
            logger.info("Tentative de réactivation de la connexion réseau...")

            # Flush DNS
            subprocess.run(['ipconfig', '/flushdns'], capture_output=True, timeout=10)

            # Renouveler l'IP
            subprocess.run(['ipconfig', '/release'], capture_output=True, timeout=10)
            time.sleep(2)
            subprocess.run(['ipconfig', '/renew'], capture_output=True, timeout=15)

            # Attendre un peu
            time.sleep(5)

            # Re-tester
            socket.create_connection(("8.8.8.8", 53), timeout=10)
            logger.info("Connexion réseau rétablie")
            return True

        except Exception as e:
            logger.error(f"Impossible de rétablir la connexion: {e}")
            return False


def full_timestamp_resync(client: Any) -> None:
    """Resynchronisation complète : Windows + Binance server time.

    Appelle ``sync_windows_silently()`` puis ``client._sync_server_time()``.
    Utilisé avant chaque envoi d'ordre REST direct.

    Parameters
    ----------
    client : BinanceFinalClient
        Instance du client Binance.
    """
    try:
        sync_windows_silently()
        time.sleep(1)
        client._sync_server_time()
        logger.info(
            "Synchronisation complète (Windows + Binance) effectuée avant envoi d'ordre."
        )
    except Exception as e:
        logger.error(f"Echec de la resynchronisation horaire: {e}")


def validate_api_connection(
    client: Any,
    send_alert_fn: Optional[Callable[..., Any]] = None,
    alert_template_fn: Optional[Callable[[str], Tuple[str, str]]] = None,
) -> bool:
    """Valide la connexion à l'API Binance via ``client.ping()``.

    En cas d'échec, envoie une alerte email si les callbacks sont fournis.

    Parameters
    ----------
    client : BinanceFinalClient
        Instance du client Binance.
    send_alert_fn : callable, optional
        ``send_alert_fn(subject=..., body_main=..., client=...)``
    alert_template_fn : callable, optional
        ``alert_template_fn(error_str) -> (subject, body)``

    Returns
    -------
    bool
        ``True`` si le ping réussit, ``False`` sinon.
    """
    try:
        client.ping()
        logger.info("Connexion API validée")
        return True
    except Exception as e:
        logger.error(f"Echec de validation API: {e}")
        if send_alert_fn and alert_template_fn:
            try:
                subj, body = alert_template_fn(str(e))
                send_alert_fn(subject=subj, body_main=body, client=client)
            except Exception as _exc:
                logger.debug("[timestamp_utils] send_alert a échoué: %s", _exc)
        return False
