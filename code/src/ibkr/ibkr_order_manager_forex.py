"""
ibkr_order_manager_forex.py — Gestion des ordres pour le Forex IBKR.

Fonctions d'exécution BUY/SELL/SL adaptées au Forex IBKR :
  - Pas de recvWindow (Binance-specific)
  - Stop-loss via StopOrder IBKR (pas STOP_LOSS_LIMIT)
  - Précision quantité basée sur tick size contrat
  - Idempotence via orderId IBKR
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger("ibkr_forex")

# Min lot IBKR Forex : 20 000 unités devise base (ex: 20 000 EUR)
_IBKR_FOREX_MIN_LOT = 20_000.0
# Tick size par défaut pour Forex IBKR (en unités devise quote)
_DEFAULT_TICK_SIZE = 0.00001

# Délai d'attente confirmation ordre (secondes)
_ORDER_WAIT_SECONDS = 5.0


def safe_forex_buy(
    client: Any,
    pair: str,
    quote_qty: float,
    *,
    current_price: float,
    min_lot: float = _IBKR_FOREX_MIN_LOT,
) -> Optional[Dict[str, Any]]:
    """Place un ordre d'achat au marché Forex IBKR.

    Args:
        client       : IBKRForexClient
        pair         : Paire IBKR, ex 'EURUSD'
        quote_qty    : Montant à investir en devise quote (ex: USD)
        current_price: Prix actuel mid pour calculer la quantité
        min_lot      : Lot minimum IBKR (défaut 20 000)

    Returns:
        Dict avec 'entry_price', 'quantity', 'order_id' ou None si échec.
    """
    if current_price <= 0:
        logger.error("[IBKR-OM] safe_forex_buy: prix nul pour %s", pair)
        return None

    # Calcul de la quantité en devise base
    raw_qty = quote_qty / current_price
    quantity = _round_to_lot(raw_qty, min_lot)

    if quantity < min_lot:
        logger.warning(
            "[IBKR-OM] Quantité calculée %.0f < min lot %.0f pour %s — BUY ignoré",
            quantity, min_lot, pair,
        )
        return None

    logger.info(
        "[IBKR-OM] BUY %s qty=%.0f (quote=%.2f, price=%.5f)",
        pair, quantity, quote_qty, current_price,
    )

    # B-02: orderRef UUID — détection doublons sur retry
    _order_ref = str(uuid.uuid4())
    try:
        result = client.order_market_buy(symbol=pair, quantity=quantity, orderRef=_order_ref)
        time.sleep(_ORDER_WAIT_SECONDS)

        # Vérifier que l'ordre a bien été exécuté (status=FILLED, executedQty > 0)
        order_status = result.get("status", "UNKNOWN")
        filled_qty = float(result.get("executedQty", "0") or "0")
        if order_status == "CANCELED" and filled_qty == 0:
            logger.error(
                "[IBKR-OM] safe_forex_buy: ordre rejeté %s status=%s filled=0 → BUY annulé",
                pair, order_status,
            )
            return None

        # Récupérer le prix de remplissage réel
        fill_price = _extract_fill_price(result, current_price)
        order_id = result.get("orderId", 0)

        logger.info(
            "[IBKR-OM] BUY exécuté : %s qty=%.0f @%.5f orderId=%s",
            pair, quantity, fill_price, order_id,
        )
        return {
            "entry_price": fill_price,
            "quantity": quantity,
            "order_id": order_id,
            "raw": result,
        }
    except Exception as exc:
        logger.error("[IBKR-OM] safe_forex_buy ERREUR %s : %s", pair, exc)
        return None


def safe_forex_sell(
    client: Any,
    pair: str,
    quantity: float,
    *,
    reason: str = "SIGNAL",
) -> Optional[Dict[str, Any]]:
    """Place un ordre de vente au marché Forex IBKR.

    Args:
        client   : IBKRForexClient
        pair     : Paire IBKR
        quantity : Quantité à vendre (en devise base)
        reason   : Raison de la vente (pour les logs)

    Returns:
        Dict avec 'exit_price', 'quantity', 'order_id' ou None si échec.
    """
    if quantity <= 0:
        logger.error("[IBKR-OM] safe_forex_sell: quantité nulle pour %s", pair)
        return None

    logger.info("[IBKR-OM] SELL %s qty=%.0f raison=%s", pair, quantity, reason)

    # C1: orderRef UUID — détection doublons sur retry
    _order_ref = str(uuid.uuid4())
    try:
        result = client.order_market_sell(symbol=pair, quantity=quantity, orderRef=_order_ref)
        time.sleep(_ORDER_WAIT_SECONDS)

        order_status = result.get("status", "UNKNOWN")
        filled_qty = float(result.get("executedQty", "0") or "0")
        if order_status == "CANCELED" and filled_qty == 0:
            logger.error(
                "[IBKR-OM] safe_forex_sell: ordre rejeté %s status=%s filled=0 → SELL annulé",
                pair, order_status,
            )
            return None

        fill_price = _extract_fill_price(result, 0.0)
        order_id = result.get("orderId", 0)

        logger.info(
            "[IBKR-OM] SELL exécuté : %s qty=%.0f @%.5f orderId=%s raison=%s",
            pair, quantity, fill_price, order_id, reason,
        )
        return {
            "exit_price": fill_price,
            "quantity": quantity,
            "order_id": order_id,
            "raw": result,
        }
    except Exception as exc:
        logger.error("[IBKR-OM] safe_forex_sell ERREUR %s : %s", pair, exc)
        return None


def place_forex_stop_loss(
    client: Any,
    pair: str,
    quantity: float,
    stop_price: float,
    lmt_price_offset: float = 0.00030,
) -> Optional[Dict[str, Any]]:
    """Place un ordre stop-loss natif sur IBKR (StopLimitOrder SELL).

    Args:
        lmt_price_offset : écart du prix limit sous le stop (C2 — défaut 3 pips).

    Returns:
        Dict avec 'sl_order_id', 'stop_price' ou None si échec.
    """
    if stop_price <= 0 or quantity <= 0:
        logger.error(
            "[IBKR-OM] place_forex_stop_loss: paramètres invalides "
            "pair=%s qty=%.0f stop=%.5f", pair, quantity, stop_price,
        )
        return None

    stop_price = round(stop_price, 5)
    logger.info(
        "[IBKR-OM] Placement SL %s qty=%.0f stop=%.5f",
        pair, quantity, stop_price,
    )

    # B-02: orderRef UUID — détection doublons sur retry
    _sl_order_ref = str(uuid.uuid4())
    try:
        result = client.create_order(
            symbol=pair,
            side="SELL",
            type="STOP_LOSS",
            quantity=quantity,
            stopPrice=stop_price,
            lmtPriceOffset=lmt_price_offset,
            orderRef=_sl_order_ref,
        )
        time.sleep(1.0)

        order_id = result.get("orderId", 0)
        logger.info(
            "[IBKR-OM] SL placé : %s stop=%.5f orderId=%s",
            pair, stop_price, order_id,
        )
        return {
            "sl_order_id": order_id,
            "stop_price": stop_price,
            "raw": result,
        }
    except Exception as exc:
        logger.error("[IBKR-OM] place_forex_stop_loss ERREUR %s : %s", pair, exc)
        return None


def cancel_forex_order(
    client: Any,
    pair: str,
    order_id: int,
) -> bool:
    """Annule un ordre IBKR par orderId.

    Returns:
        True si annulation réussie ou ordre déjà inexistant, False si erreur.
    """
    if not order_id:
        return True
    try:
        result = client.cancel_order(symbol=pair, orderId=order_id)
        status = result.get("status", "")
        if status in ("CANCELED", "NOT_FOUND"):
            logger.info("[IBKR-OM] Ordre %s annulé (status=%s)", order_id, status)
            return True
        logger.warning("[IBKR-OM] cancel_order status inattendu : %s", status)
        return False
    except Exception as exc:
        logger.error("[IBKR-OM] cancel_forex_order ERREUR orderId=%s : %s", order_id, exc)
        return False


def get_current_price(client: Any, pair: str) -> float:
    """Retourne le prix mid actuel d'une paire Forex IBKR."""
    try:
        ticker = client.get_symbol_ticker(symbol=pair)
        return float(ticker.get("price", 0.0))
    except Exception as exc:
        logger.error("[IBKR-OM] get_current_price ERREUR %s : %s", pair, exc)
        return 0.0


def get_account_nav(client: Any) -> float:
    """Retourne la valeur liquidative nette du compte IBKR (en devise du compte)."""
    try:
        return client.get_account_balance()
    except Exception as exc:
        logger.error("[IBKR-OM] get_account_nav ERREUR : %s", exc)
        return 0.0


def safe_forex_short_open(
    client: Any,
    pair: str,
    quote_qty: float,
    *,
    current_price: float,
    min_lot: float = _IBKR_FOREX_MIN_LOT,
) -> Optional[Dict[str, Any]]:
    """Ouvre une position SHORT Forex IBKR via un ordre SELL au marché.

    Args:
        client       : IBKRForexClient
        pair         : Paire IBKR, ex 'EURUSD'
        quote_qty    : Montant à shorter en devise quote (ex: USD)
        current_price: Prix actuel mid pour calculer la quantité
        min_lot      : Lot minimum IBKR (défaut 20 000)

    Returns:
        Dict avec 'entry_price', 'quantity', 'order_id' ou None si échec.
    """
    if current_price <= 0:
        logger.error("[IBKR-OM] safe_forex_short_open: prix nul pour %s", pair)
        return None

    raw_qty = quote_qty / current_price
    quantity = _round_to_lot(raw_qty, min_lot)

    if quantity < min_lot:
        logger.warning(
            "[IBKR-OM] Quantité calculée %.0f < min lot %.0f pour %s — SHORT ignoré",
            quantity, min_lot, pair,
        )
        return None

    logger.info(
        "[IBKR-OM] SHORT OPEN %s qty=%.0f (quote=%.2f, price=%.5f)",
        pair, quantity, quote_qty, current_price,
    )

    # C1: orderRef UUID — détection doublons sur retry
    _order_ref = str(uuid.uuid4())
    try:
        result = client.order_market_sell(symbol=pair, quantity=quantity, orderRef=_order_ref)
        time.sleep(_ORDER_WAIT_SECONDS)

        order_status = result.get("status", "UNKNOWN")
        filled_qty = float(result.get("executedQty", "0") or "0")
        if order_status == "CANCELED" and filled_qty == 0:
            logger.error(
                "[IBKR-OM] safe_forex_short_open: ordre rejeté %s status=%s filled=0 → SHORT annulé",
                pair, order_status,
            )
            return None

        fill_price = _extract_fill_price(result, current_price)
        order_id = result.get("orderId", 0)

        logger.info(
            "[IBKR-OM] SHORT ouvert : %s qty=%.0f @%.5f orderId=%s",
            pair, quantity, fill_price, order_id,
        )
        return {
            "entry_price": fill_price,
            "quantity": quantity,
            "order_id": order_id,
            "raw": result,
        }
    except Exception as exc:
        logger.error("[IBKR-OM] safe_forex_short_open ERREUR %s : %s", pair, exc)
        return None


def place_forex_stop_buy(
    client: Any,
    pair: str,
    quantity: float,
    stop_price: float,
    lmt_price_offset: float = 0.00030,
) -> Optional[Dict[str, Any]]:
    """Place un ordre stop-loss natif pour une position SHORT (StopLimitOrder BUY).

    Args:
        lmt_price_offset : écart du prix limit au-dessus du stop (C2 — défaut 3 pips).

    Returns:
        Dict avec 'sl_order_id', 'stop_price' ou None si échec.
    """
    if stop_price <= 0 or quantity <= 0:
        logger.error(
            "[IBKR-OM] place_forex_stop_buy: paramètres invalides "
            "pair=%s qty=%.0f stop=%.5f", pair, quantity, stop_price,
        )
        return None

    stop_price = round(stop_price, 5)
    logger.info(
        "[IBKR-OM] Placement SL SHORT %s qty=%.0f stop=%.5f",
        pair, quantity, stop_price,
    )

    # C1: orderRef UUID — détection doublons sur retry
    _sl_order_ref = str(uuid.uuid4())
    try:
        result = client.create_order(
            symbol=pair,
            side="BUY",
            type="STOP_LOSS",
            quantity=quantity,
            stopPrice=stop_price,
            lmtPriceOffset=lmt_price_offset,
            orderRef=_sl_order_ref,
        )
        time.sleep(1.0)

        order_id = result.get("orderId", 0)
        logger.info(
            "[IBKR-OM] SL SHORT placé : %s stop=%.5f orderId=%s",
            pair, stop_price, order_id,
        )
        return {
            "sl_order_id": order_id,
            "stop_price": stop_price,
            "raw": result,
        }
    except Exception as exc:
        logger.error("[IBKR-OM] place_forex_stop_buy ERREUR %s : %s", pair, exc)
        return None


def safe_forex_cover(
    client: Any,
    pair: str,
    quantity: float,
    *,
    reason: str = "COVER",
) -> Optional[Dict[str, Any]]:
    """Ferme une position SHORT Forex IBKR via un ordre BUY au marché.

    Args:
        client   : IBKRForexClient
        pair     : Paire IBKR
        quantity : Quantité à racheter (en devise base)
        reason   : Raison du rachat (pour les logs)

    Returns:
        Dict avec 'exit_price', 'quantity', 'order_id' ou None si échec.
    """
    if quantity <= 0:
        logger.error("[IBKR-OM] safe_forex_cover: quantité nulle pour %s", pair)
        return None

    logger.info("[IBKR-OM] COVER %s qty=%.0f raison=%s", pair, quantity, reason)

    # C1: orderRef UUID — détection doublons sur retry
    _order_ref = str(uuid.uuid4())
    try:
        result = client.order_market_buy(symbol=pair, quantity=quantity, orderRef=_order_ref)
        time.sleep(_ORDER_WAIT_SECONDS)

        order_status = result.get("status", "UNKNOWN")
        filled_qty = float(result.get("executedQty", "0") or "0")
        if order_status == "CANCELED" and filled_qty == 0:
            logger.error(
                "[IBKR-OM] safe_forex_cover: ordre rejeté %s status=%s filled=0 → COVER annulé",
                pair, order_status,
            )
            return None

        fill_price = _extract_fill_price(result, 0.0)
        order_id = result.get("orderId", 0)

        logger.info(
            "[IBKR-OM] COVER exécuté : %s qty=%.0f @%.5f orderId=%s raison=%s",
            pair, quantity, fill_price, order_id, reason,
        )
        return {
            "exit_price": fill_price,
            "quantity": quantity,
            "order_id": order_id,
            "raw": result,
        }
    except Exception as exc:
        logger.error("[IBKR-OM] safe_forex_cover ERREUR %s : %s", pair, exc)
        return None


# ─── Helpers privés ──────────────────────────────────────────────────────────

def _round_to_lot(qty: float, lot_size: float) -> float:
    """Arrondit la quantité au lot IBKR inférieur."""
    import math
    if lot_size <= 0:
        return qty
    if math.isnan(qty) or math.isinf(qty):
        return 0.0
    return math.floor(qty / lot_size) * lot_size


def _extract_fill_price(order_result: Dict[str, Any], fallback: float) -> float:
    """A1: Extrait le prix de remplissage moyen — lit avgFillPrice en priorité."""
    for field in ("avgFillPrice", "price"):
        try:
            val_str = order_result.get(field, "")
            if val_str and float(val_str) > 0:
                return float(val_str)
        except (ValueError, TypeError):
            continue
    return fallback
