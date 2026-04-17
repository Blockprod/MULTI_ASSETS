"""
state_manager.py — Gestion de l'état persistant du bot.

Contient:
- save_state / load_state (JSON) — fonctions pures acceptant des paramètres
- Intégrité HMAC pour protéger contre la corruption/falsification
- C-17: Migration pickle → JSON avec validation de schéma
- Migration automatique des anciens fichiers pickle (HMAC_V1 / non-signé)
"""
import os
import json
import hmac as hmac_mod
import hashlib
import pickle
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Dict, Set

from bot_config import config
from exceptions import StateError

logger = logging.getLogger('trading_bot')

# Clé HMAC dérivée du secret API — P0-04: aucun fallback hardcodé permis.
# Si BINANCE_SECRET_KEY est absent, le bot ne doit pas démarrer.
_secret_key_env = os.environ.get('BINANCE_SECRET_KEY')
if not _secret_key_env:
    raise EnvironmentError(
        "[P0-04] Variable d'environnement BINANCE_SECRET_KEY manquante. "
        "Le bot ne peut pas démarrer sans une clé HMAC sécurisée pour l'état."
    )
_HMAC_KEY = _secret_key_env.encode('utf-8')

# P0-01: répertoire et fichier d'état effectifs — config.states_dir/state_file par défaut,
# surchargés uniquement en tests (monkeypatch). Évite toute mutation du singleton Config.
_effective_states_dir: str = ""
_effective_state_file: str = ""


def _get_state_path() -> str:
    """Retourne le chemin complet du fichier d'état (effectif ou config par défaut)."""
    states_dir = _effective_states_dir if _effective_states_dir else config.states_dir
    state_file = _effective_state_file if _effective_state_file else config.state_file
    return os.path.join(states_dir, state_file)


def _get_states_dir() -> str:
    """Retourne le répertoire d'état effectif."""
    return _effective_states_dir if _effective_states_dir else config.states_dir


def _compute_hmac(data: bytes) -> bytes:
    """Calcule un HMAC-SHA256 des données."""
    return hmac_mod.new(_HMAC_KEY, data, hashlib.sha256).digest()


def _STATE_HEADER() -> bytes:
    """Marqueur de format pickle signé (legacy)."""
    return b'HMAC_V1:'


def _JSON_HEADER() -> bytes:
    """Marqueur de format JSON signé (C-17)."""
    return b'JSON_V1:'


# ─── C-17: Schema validation ─────────────────────────────────────────────────
# Clés connues de PairState (C-16). Les clés absentes ne sont pas un problème
# (total=False), mais les clés inconnues sont loguées en warning.
_KNOWN_PAIR_KEYS: Set[str] = {
    'last_run_time', 'last_best_params', 'execution_count', 'last_execution',
    'last_order_side', 'entry_price', 'initial_position_size',  # 'in_position' supprimé C-06
    'atr_at_entry', 'stop_loss', 'stop_loss_at_entry',
    'trailing_activation_price_at_entry', 'trailing_activation_price',
    'trailing_stop_activated', 'trailing_stop', 'max_price', 'sl_order_id',
    'sl_exchange_placed',
    'partial_enabled', 'partial_taken_1', 'partial_taken_2',
    'breakeven_triggered',
    'entry_scenario', 'entry_timeframe', 'entry_ema1', 'entry_ema2',
    'buy_timestamp',
    '_stop_loss_cooldown_until',
    'oos_blocked', 'oos_blocked_since',
    'drawdown_halted',                         # ST-P2-02
    'quote_currency', 'ticker_spot_price', 'latest_best_params',
}

# Clés globales connues de BotStateDict (C-16).
_KNOWN_GLOBAL_KEYS: Set[str] = {
    'emergency_halt', 'emergency_halt_reason',
    '_daily_pnl_tracker', '_state_version',
    'reconcile_failed',                        # TS-P2-02
}


def validate_bot_state(state: Dict) -> None:
    """Valide le schéma de bot_state et logue les clés inconnues (C-17).

    Ne lève jamais d'exception — validation informative uniquement.
    """
    for key, value in state.items():
        if key in _KNOWN_GLOBAL_KEYS:
            continue
        if isinstance(value, dict):
            # Clé dynamique = nom de paire → valider les sous-clés
            unknown = set(value.keys()) - _KNOWN_PAIR_KEYS
            if unknown:
                logger.warning(
                    "[STATE C-17] Paire '%s' contient des clés inconnues: %s "
                    "(migration ou version future ?)",
                    key, sorted(unknown),
                )
        else:
            # Clé scalaire non reconnue au top-level
            logger.warning(
                "[STATE C-17] Clé globale inconnue dans bot_state: '%s' (type=%s)",
                key, type(value).__name__,
            )


# ─── C-17: JSON encoder pour types non-natifs ────────────────────────────────

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


def save_state(bot_state: Dict):
    """Sauvegarde l'état du bot sur disque au format JSON signé (C-17).

    P0-SAVE: pas de @log_exceptions — les erreurs doivent remonter au caller
    pour déclencher les alertes et le kill-switch si nécessaire.

    Args:
        bot_state: dictionnaire d'état à persister.

    Raises:
        StateError: si la sauvegarde échoue (disque, permissions, sérialisation).
    """
    try:
        os.makedirs(_get_states_dir(), exist_ok=True)
        state_path = _get_state_path()
        state_bytes = json.dumps(bot_state, cls=_StateEncoder,
                                 ensure_ascii=False, indent=2).encode('utf-8')
        old_hash = None
        if os.path.exists(state_path):
            with open(state_path, 'rb') as f:
                raw_old = f.read()
            # Extraire uniquement les bytes de données pour comparaison
            json_header = _JSON_HEADER()
            pickle_header = _STATE_HEADER()
            if raw_old.startswith(json_header):
                old_state_bytes = raw_old[len(json_header) + 32:]
            elif raw_old.startswith(pickle_header):
                old_state_bytes = raw_old[len(pickle_header) + 32:]
            else:
                old_state_bytes = raw_old
            old_hash = hash(old_state_bytes)
        new_hash = hash(state_bytes)
        if old_hash != new_hash:
            # Signer les données avec HMAC avant écriture
            mac = _compute_hmac(state_bytes)
            signed_data = _JSON_HEADER() + mac + state_bytes
            tmp_path = state_path + '.tmp'
            try:
                with open(tmp_path, 'wb') as f:
                    f.write(signed_data)
                os.replace(tmp_path, state_path)  # écriture atomique – résiste aux crashs
            except Exception:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception as _exc:
                        logger.debug("[state_manager] Impossible de supprimer le fichier tmp: %s", _exc)
                raise
            logger.debug("État du bot sauvegardé (JSON, modifié)")
        else:
            logger.debug("État du bot inchangé, pas de sauvegarde")
    except (OSError, TypeError, ValueError) as e:
        raise StateError(f"Erreur lors de la sauvegarde de l'état: {e}") from e
    except Exception as e:
        # P1-06: re-raise au lieu de swallow — le caller doit savoir que la sauvegarde a échoué
        logger.error(f"Erreur inattendue lors de la sauvegarde: {e}")
        raise StateError(f"Erreur inattendue lors de la sauvegarde: {e}") from e


# P1-06: pas de @log_exceptions — les erreurs doivent remonter au caller
def load_state() -> Dict:
    """Charge l'état du bot depuis le disque.

    C-17: Supporte trois formats (détection automatique) :
    1. JSON signé (JSON_V1: header) — format actuel
    2. Pickle signé (HMAC_V1: header) — migration automatique
    3. Pickle non signé (ancien format) — migration silencieuse

    Returns:
        Dictionnaire d'état chargé, ou {} si aucun fichier.
    """
    try:
        state_path = _get_state_path()
        if not os.path.exists(state_path):
            return {}

        with open(state_path, 'rb') as f:
            raw = f.read()

        json_header = _JSON_HEADER()
        pickle_header = _STATE_HEADER()

        if raw.startswith(json_header):
            # ── Format JSON signé (actuel) ──
            mac_stored = raw[len(json_header):len(json_header) + 32]
            state_bytes = raw[len(json_header) + 32:]
            mac_computed = _compute_hmac(state_bytes)
            if not hmac_mod.compare_digest(mac_stored, mac_computed):
                raise StateError("Intégrité du fichier d'état compromise (HMAC invalide). "
                                 "Le fichier a été modifié ou corrompu.")
            loaded = json.loads(state_bytes.decode('utf-8'))

        elif raw.startswith(pickle_header):
            # ── Format pickle signé (HMAC_V1) — migration C-17 ──
            mac_stored = raw[len(pickle_header):len(pickle_header) + 32]
            state_bytes = raw[len(pickle_header) + 32:]
            mac_computed = _compute_hmac(state_bytes)
            if not hmac_mod.compare_digest(mac_stored, mac_computed):
                raise StateError("Intégrité du fichier d'état compromise (HMAC invalide). "
                                 "Le fichier a été modifié ou corrompu.")
            loaded = pickle.loads(state_bytes)
            logger.warning(
                "[STATE C-17] Ancien format pickle signé détecté — "
                "migration automatique vers JSON au prochain save."
            )

        else:
            # ── Format sans header — détecter JSON vs pickle ──
            stripped = raw.lstrip()
            if stripped and stripped[0:1] in (b'{', b'['):
                # Plain JSON (pas de header JSON_V1) — ex: fichier édité à la main
                logger.warning(
                    "État chargé en JSON sans signature HMAC. "
                    "Il sera re-signé au prochain save."
                )
                loaded = json.loads(raw.decode('utf-8'))
            else:
                # Pickle non signé (ancien format)
                logger.warning(
                    "État chargé sans HMAC (ancien format pickle). "
                    "Il sera re-signé au prochain save."
                )
                loaded = pickle.loads(raw)

        # F-SL-FIX: Migration format legacy — si une clé "bot_state" dict existe
        # au top-level, dé-imbriquer les paires vers le top-level.
        # Ancien format: {"bot_state": {"SOLUSDT": {...}}, "_state_version": ...}
        # Nouveau format: {"SOLUSDT": {...}, "_state_version": ...}
        if isinstance(loaded, dict) and 'bot_state' in loaded and isinstance(loaded['bot_state'], dict):
            _inner = loaded.pop('bot_state')
            for _k, _v in _inner.items():
                if _k not in loaded:
                    loaded[_k] = _v
                else:
                    logger.warning(
                        "[STATE F-SL-FIX] Clé dupliquée '%s' ignorée lors de la migration "
                        "du format imbriqué — la version top-level est conservée.", _k,
                    )
            logger.warning(
                "[STATE F-SL-FIX] Format legacy détecté: clé 'bot_state' imbriquée migrée "
                "vers le top-level (%d paires extraites). Re-signé au prochain save.", len(_inner),
            )

        # C-17: Validation de schéma (informative, non-bloquante)
        if isinstance(loaded, dict):
            validate_bot_state(loaded)

        logger.info("État du bot chargé")
        return loaded

    except (OSError, json.JSONDecodeError, pickle.UnpicklingError, EOFError) as e:
        raise StateError(f"Erreur lors du chargement de l'état: {e}") from e
    except Exception as e:
        # P1-06: re-raise — un fichier corrompu ne doit pas être masqué
        logger.error(f"Erreur inattendue lors du chargement: {e}")
        raise StateError(f"Erreur inattendue lors du chargement: {e}") from e


# ─── C-13: Centralized state mutation helpers ─────────────────────────────────

def set_emergency_halt(bot_state: Dict, reason: str) -> None:
    """Active l'arrêt d'urgence du bot de manière atomique (C-13).

    Doit être appelée depuis un contexte protégé par _bot_state_lock.

    Args:
        bot_state: dictionnaire d'état global du bot.
        reason: description courte de la cause du halt.
    """
    bot_state['emergency_halt'] = True
    bot_state['emergency_halt_reason'] = reason
    logger.critical("[EMERGENCY HALT] Activé — %s", reason)


def update_pair_state(bot_state: Dict, pair: str, **kwargs: Any) -> None:
    """Met à jour atomiquement une ou plusieurs clés du pair_state (C-13).

    Doit être appelée depuis un contexte protégé par _bot_state_lock.

    Args:
        bot_state: dictionnaire d'état global du bot.
        pair: clé de paire dans bot_state (ex: 'BTCUSDC').
        **kwargs: clés/valeurs à mettre à jour dans pair_state.
    """
    bot_state.setdefault(pair, {}).update(kwargs)
