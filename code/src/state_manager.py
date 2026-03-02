"""
state_manager.py — Gestion de l'état persistant du bot.

Contient:
- save_state / load_state (pickle) — fonctions pures acceptant des paramètres
- Intégrité HMAC pour protéger contre la corruption/falsification
"""
import os
import hmac as hmac_mod
import hashlib
import pickle
import logging
from typing import Dict

from bot_config import config, log_exceptions
from exceptions import StateError

logger = logging.getLogger('trading_bot')

# Clé HMAC dérivée du secret API (ou fallback fixe si absent)
_HMAC_KEY = os.environ.get('BINANCE_SECRET_KEY', 'multi_assets_state_integrity_key').encode('utf-8')


def _compute_hmac(data: bytes) -> bytes:
    """Calcule un HMAC-SHA256 des données."""
    return hmac_mod.new(_HMAC_KEY, data, hashlib.sha256).digest()


def _STATE_HEADER() -> bytes:
    """Marqueur de format pour distinguer les fichiers signés."""
    return b'HMAC_V1:'


@log_exceptions(default_return=None)
def save_state(bot_state: Dict):
    """Sauvegarde l'état du bot sur disque.

    Args:
        bot_state: dictionnaire d'état à persister.
    """
    try:
        os.makedirs(config.states_dir, exist_ok=True)
        state_path = os.path.join(config.states_dir, config.state_file)
        state_bytes = pickle.dumps(bot_state)
        old_hash = None
        if os.path.exists(state_path):
            with open(state_path, 'rb') as f:
                old_hash = hash(f.read())
        new_hash = hash(state_bytes)
        if old_hash != new_hash:
            # Signer les données avec HMAC avant écriture
            mac = _compute_hmac(state_bytes)
            signed_data = _STATE_HEADER() + mac + state_bytes
            tmp_path = state_path + '.tmp'
            try:
                with open(tmp_path, 'wb') as f:
                    f.write(signed_data)
                os.replace(tmp_path, state_path)  # écriture atomique – résiste aux crashs
            except Exception:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                raise
            logger.debug("État du bot sauvegardé (modifié)")
        else:
            logger.debug("État du bot inchangé, pas de sauvegarde")
    except (OSError, pickle.PicklingError) as e:
        raise StateError(f"Erreur lors de la sauvegarde de l'état: {e}") from e
    except Exception as e:
        logger.error(f"Erreur inattendue lors de la sauvegarde: {e}")


@log_exceptions(default_return={})
def load_state() -> Dict:
    """Charge l'état du bot depuis le disque.

    Returns:
        Dictionnaire d'état chargé, ou {} si aucun fichier.
    """
    try:
        state_path = os.path.join(config.states_dir, config.state_file)
        if os.path.exists(state_path):
            with open(state_path, 'rb') as f:
                raw = f.read()
            header = _STATE_HEADER()
            if raw.startswith(header):
                # Format signé : header (8 octets) + HMAC (32 octets) + pickle
                mac_stored = raw[len(header):len(header) + 32]
                state_bytes = raw[len(header) + 32:]
                mac_computed = _compute_hmac(state_bytes)
                if not hmac_mod.compare_digest(mac_stored, mac_computed):
                    raise StateError("Intégrité du fichier d'état compromise (HMAC invalide). "
                                     "Le fichier a été modifié ou corrompu.")
                loaded = pickle.loads(state_bytes)
            else:
                # Ancien format non signé — migration silencieuse
                logger.warning("État chargé sans HMAC (ancien format). Il sera re-signé au prochain save.")
                loaded = pickle.loads(raw)
            logger.info("État du bot chargé")
            return loaded
        return {}
    except (OSError, pickle.UnpicklingError, EOFError) as e:
        raise StateError(f"Erreur lors du chargement de l'état: {e}") from e
    except Exception as e:
        logger.error(f"Erreur inattendue lors du chargement: {e}")
        return {}
