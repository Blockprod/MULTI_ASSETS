"""
email_templates.py — Templates d'emails pour le bot de trading.
Phase 4 refactoring: centralise tous les corps d'emails.

Chaque fonction retourne un tuple (subject, body) prêt à être envoyé
via send_email_alert() ou send_trading_alert_email().
"""
from datetime import datetime
from typing import Optional, Tuple


def _timestamp() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ─── Ordres ────────────────────────────────────────────────────────────────

def order_error_email(side: str, error_code, error_msg: str, params) -> Tuple[str, str]:
    """Erreur lors de l'exécution d'un ordre (BUY/SELL/STOP_LOSS/TRAILING)."""
    return (
        f"[CRIT] ERREUR EXECUTION {side.upper()} ORDER",
        f"Erreur lors de l'execution de l'ordre {side.upper()} : {error_code} - {error_msg}\n\nParams : {params}"
    )


def order_success_email(side: str, params, result) -> Tuple[str, str]:
    """Ordre exécuté avec succès."""
    return (
        f"[INFO] {side.upper()} ORDER EXECUTE",
        f"Ordre {side.upper()} exécuté avec succès.\n\nParams : {params}\nRéponse : {result}"
    )


def order_exception_email(side: str, error, params) -> Tuple[str, str]:
    """Exception Python pendant l'appel API d'ordre."""
    return (
        f"[CRIT] EXCEPTION {side.upper()} ORDER",
        f"Exception lors de l'appel API {side.upper()} : {error}\n\nParams : {params}"
    )


def trailing_stop_error_email(error_code, error_msg: str, params) -> Tuple[str, str]:
    return (
        "[CRIT] ERREUR EXECUTION TRAILING STOP",
        f"Erreur TRAILING STOP: {error_code} - {error_msg}\n\nParams: {params}"
    )


def trailing_stop_success_email(params, result) -> Tuple[str, str]:
    return (
        "[INFO] TRAILING STOP EXECUTE",
        f"TRAILING STOP exécuté.\n\nParams: {params}\nRéponse: {result}"
    )


def stop_loss_error_email(error_code, error_msg: str, params) -> Tuple[str, str]:
    return (
        "[CRIT] ERREUR EXECUTION STOP LOSS",
        f"Erreur STOP LOSS: {error_code} - {error_msg}\n\nParams: {params}"
    )


def stop_loss_success_email(params, result) -> Tuple[str, str]:
    return (
        "[INFO] STOP LOSS EXECUTE",
        f"STOP LOSS exécuté.\n\nParams: {params}\nRéponse: {result}"
    )


# ─── Connexion / Réseau ───────────────────────────────────────────────────

def api_connection_failure_email(error: str) -> Tuple[str, str]:
    """Échec de connexion à l'API Binance."""
    error_short = str(error)[:150] + ('...' if len(str(error)) > 150 else '')
    return (
        "[CRIT] ERREUR Connexion API",
        f"""=== ECHEC DE CONNEXION API BINANCE ===

Le bot n'a pas pu etablir une connexion avec l'API Binance.

DETAILS DE L'INCIDENT:
----------------------
Horodatage          : {_timestamp()}
Erreur rencontree   : {error_short}
Type d'erreur       : Connexion API

IMPACT SUR LE BOT:
------------------
Le bot ne peut pas fonctionner sans connexion API.
Toutes les operations de trading sont suspendues.

ACTIONS RECOMMANDEES:
---------------------
1. Verifier la connexion internet
2. Controler les cles API Binance
3. Verifier le statut de l'API Binance
4. Redemarrer le bot apres resolution

--- Message automatique du Bot de Trading Crypto ---"""
    )


def data_retrieval_error_email(pair_symbol: str, time_interval: str,
                               start_date: str, error: str) -> Tuple[str, str]:
    """Erreur lors de la récupération des données historiques."""
    error_short = str(error)[:200] + ('...' if len(str(error)) > 200 else '')
    return (
        f"[WARN] ERREUR Donnees - {pair_symbol}",
        f"""=== ERREUR RECUPERATION DONNEES ===

Le bot n'a pas pu recuperer les donnees historiques.

DETAILS DE L'INCIDENT:
----------------------
Horodatage          : {_timestamp()}
Paire               : {pair_symbol}
Timeframe           : {time_interval}
Période             : depuis {start_date}
Erreur rencontree   : {error_short}

IMPACT SUR LE BOT:
------------------
L'analyse de {pair_symbol} est temporairement indisponible.
Le bot continuera avec les autres paires si disponibles.

--- Message automatique du Bot de Trading Crypto ---"""
    )


def network_error_email(pair_symbol: str, error: str) -> Tuple[str, str]:
    """Erreur réseau pendant le téléchargement de données."""
    return (
        f"[WARN] ERREUR Reseau - {pair_symbol}",
        f"""=== ERREUR RESEAU ===

Problème de connectivité détecté lors du téléchargement des données.

DETAILS DE L'INCIDENT:
----------------------
Horodatage          : {_timestamp()}
Paire               : {pair_symbol}
Erreur              : {str(error)[:200]}

ACTIONS RECOMMANDEES:
---------------------
1. Vérifier la connexion internet
2. Le bot réessaiera automatiquement

--- Message automatique du Bot de Trading Crypto ---"""
    )


def indicator_error_email(error: str) -> Tuple[str, str]:
    """Erreur critique lors du calcul des indicateurs."""
    return (
        "[CRIT] ERREUR CRITIQUE Indicateurs",
        f"""=== ERREUR CRITIQUE CALCUL INDICATEURS ===

Une erreur critique s'est produite lors du calcul des indicateurs techniques.

DETAILS DE L'INCIDENT:
----------------------
Horodatage          : {_timestamp()}
Erreur              : {str(error)[:300]}

IMPACT:
-------
Le trading est temporairement suspendu pour cette paire.

--- Message automatique du Bot de Trading Crypto ---"""
    )


# ─── Trades (détaillés) ───────────────────────────────────────────────────

def buy_executed_email(pair: str, qty: float, price: float,
                       usdc_spent: float, usdc_balance_after: float,
                       strategy: str = "Multi-Timeframe EMA/StochRSI",
                       extra_details: str = "") -> Tuple[str, str]:
    """Email après achat réussi."""
    return (
        f"[INFO] Achat execute - {pair}",
        f"""=== ACHAT EXECUTE ===

Horodatage          : {_timestamp()}
Paire               : {pair}
Quantite            : {qty:.8f}
Prix d'entree       : {price:.4f} USDC
Montant investi     : {usdc_spent:.2f} USDC
Solde USDC apres    : {usdc_balance_after:.2f} USDC
Strategie           : {strategy}
{extra_details}
--- Message automatique du Bot de Trading Crypto ---"""
    )


def sell_executed_email(pair: str, qty: float, price: float,
                        usdc_received: float, sell_reason: str,
                        pnl_pct: Optional[float] = None,
                        strategy: str = "Multi-Timeframe EMA/StochRSI",
                        extra_details: str = "") -> Tuple[str, str]:
    """Email après vente réussie (signal ou stop-loss)."""
    pnl_str = f"P&L             : {pnl_pct:+.2f}%" if pnl_pct is not None else ""
    return (
        f"[INFO] Vente executee - {pair} ({sell_reason})",
        f"""=== VENTE EXECUTEE ===

Horodatage          : {_timestamp()}
Paire               : {pair}
Quantite vendue     : {qty:.8f}
Prix de sortie      : {price:.4f} USDC
Montant recu        : {usdc_received:.2f} USDC
Raison              : {sell_reason}
{pnl_str}
Strategie           : {strategy}
{extra_details}
--- Message automatique du Bot de Trading Crypto ---"""
    )


# ─── Erreurs génériques ───────────────────────────────────────────────────

def trading_execution_error_email(error: str, traceback_str: str = "") -> Tuple[str, str]:
    """Erreur pendant l'exécution du trading."""
    tb = traceback_str[:500] if traceback_str else ""
    return (
        "[WARN] Erreur execution trading",
        f"Erreur lors de l'exécution du trading:\n{error}\n\n{tb}"
    )


def trading_pair_error_email(pair: str, error: str, traceback_str: str = "") -> Tuple[str, str]:
    """Erreur pendant le trading d'une paire spécifique."""
    tb = traceback_str[:500] if traceback_str else ""
    return (
        f"[WARN] ERREUR Trading - {pair}",
        f"""=== ERREUR TRADING ===

DETAILS DE L'INCIDENT:
----------------------
Horodatage          : {_timestamp()}
Paire concernee     : {pair}
Erreur              : {str(error)[:300]}

Traceback:
{tb}

Le bot continue avec les autres paires.

--- Message automatique du Bot de Trading Crypto ---"""
    )


def critical_startup_error_email(error: str, traceback_str: str = "") -> Tuple[str, str]:
    """Erreur critique au démarrage du bot."""
    tb = traceback_str[:500] if traceback_str else ""
    return (
        "[CRIT] ARRET CRITIQUE",
        f"Le bot s'est arrêté suite à une erreur critique:\n{error}\n\n{tb}"
    )


def generic_exception_email(func_name: str, error, args=None, kwargs=None) -> Tuple[str, str]:
    """Exception dans une fonction décorée @log_exceptions."""
    _SENSITIVE = {'api_key', 'secret_key', 'secret', 'password', 'token'}
    safe_args = str(args)[:200] if args is not None else "None"
    safe_kwargs = (
        {k: '***' if k in _SENSITIVE else v for k, v in kwargs.items()}
        if isinstance(kwargs, dict) else str(kwargs)[:200]
    ) if kwargs is not None else "None"
    return (
        f"[CRIT] EXCEPTION: {func_name}",
        f"Exception dans {func_name}: {error}\n\nArgs: {safe_args}\nKwargs: {safe_kwargs}"
    )


def cache_cleanup_email(cleaned_count: int, size_mb: float) -> Tuple[str, str]:
    """Notification après nettoyage mensuel du cache."""
    return (
        "[INFO] Nettoyage Cache",
        f"""=== NETTOYAGE MENSUEL DU CACHE EFFECTUE ===

Le nettoyage automatique du cache a ete realise avec succes.

DETAILS DE L'OPERATION:
-----------------------
Horodatage          : {_timestamp()}
Fichiers supprimes  : {cleaned_count}
Espace libere       : {size_mb:.1f} MB
Type d'operation    : Nettoyage automatique

FONCTIONNEMENT NORMAL:
----------------------
Les donnees seront re-telechargees automatiquement.
Aucune intervention manuelle requise.

--- Message automatique du Bot de Trading Crypto ---"""
    )


# ─── Error handler ────────────────────────────────────────────────────────

def error_handler_alert_body(
    inner_body: str,
    mode_value: str,
    timeout_seconds: float,
    error_details: Optional[dict] = None,
) -> str:
    """Corps d'email pour send_alert_email() dans ErrorHandler (EM-P2-04)."""
    text = f"""
ALERTE ERREUR DU BOT DE TRADING

Heure: {_timestamp()}
Mode: {mode_value}

DETAILS:
{inner_body}

ACTION RECOMMANDEE:
1. Consulter les logs en temps reel
2. Verifier l'etat du bot
3. Corriger manuellement si necessaire
4. Le bot reprendra apres {int(timeout_seconds)}s si pas d'intervention

---
Message automatique du Bot de Trading Crypto
    """
    if error_details:
        import json
        text += f"\n\nDETAILS TECHNIQUES:\n{json.dumps(error_details, indent=2, default=str)}"
    return text


def handle_error_alert(
    context: str,
    error_type: str,
    error_msg: str,
    mode_value: str,
    failure_count: int,
    critical: bool = False,
) -> Tuple[str, str]:
    """Sujet + corps pour les alertes de handle_error() (EM-P2-04)."""
    severity = "[CRIT]" if critical else "[WARN]"
    subject = f"{severity} ERREUR CRITIQUE - {context}" if critical else f"{severity} Erreur detectee - {context}"
    body = (
        f"Contexte: {context}\n"
        f"Erreur: {error_type}\n"
        f"Message: {error_msg[:200]}\n\n"
        f"Mode Circuit: {mode_value}\n"
        f"Nombre d'erreurs: {failure_count}"
    )
    return subject, body
