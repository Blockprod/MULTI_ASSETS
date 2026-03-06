"""
tests/test_exchange_client.py — C-06 : Tests complémentaires exchange_client.py

Couvre les cas spécifiques requis par le plan de production C-06 :
- C-02 validation : place_exchange_stop_loss envoie type=STOP_LOSS (pas STOP_LOSS_LIMIT)
- place_stop_loss_order : succès + échec avec alerte email
- place_exchange_stop_loss : alerte email sur échec / succès
- _direct_market_order : alerte email sur erreur et sur succès
- BinanceFinalClient.close_connection : guard AttributeError

Tous les tests utilisent des mocks — aucune connexion réelle à Binance.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'code', 'src')
sys.path.insert(0, SRC_DIR)


# ─── Helper ─────────────────────────────────────────────────────────────────

def _make_mock_client():
    """Crée un client mock pour les tests."""
    client = MagicMock()
    client.api_key = 'test_api_key'
    client.api_secret = 'test_secret_key_for_hmac'
    client._server_time_offset = -2000
    client._sync_server_time.return_value = None
    client.get_symbol_info.return_value = {
        'filters': [
            {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
            {'filterType': 'LOT_SIZE', 'minQty': '0.001', 'stepSize': '0.001'},
            {'filterType': 'MIN_NOTIONAL', 'minNotional': '10.0'},
        ]
    }
    return client


# ══════════════════════════════════════════════════════════════════════════════
# 1.  C-02 Validation : STOP_LOSS type confirmation
# ══════════════════════════════════════════════════════════════════════════════

class TestC02StopLossTypeConfirmation:
    """Vérifie que place_exchange_stop_loss envoie type=STOP_LOSS et non STOP_LOSS_LIMIT."""

    @patch('exchange_client._api_rate_limiter')
    def test_api_call_sends_stop_loss_type(self, mock_limiter):
        """Le corps de la requête POST doit contenir type=STOP_LOSS."""
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 111, 'status': 'NEW'}

        with patch('exchange_client.requests.post', return_value=mock_resp) as mock_post:
            place_exchange_stop_loss(client, 'BTCUSDC', '0.001', 50000.0)

        # Vérifier que la requête POST contient type=STOP_LOSS
        call_args = mock_post.call_args
        posted_data = call_args[1].get('data') or call_args[0][1] if len(call_args[0]) > 1 else call_args[1]['data']
        assert 'type=STOP_LOSS' in posted_data, (
            f"C-02: type=STOP_LOSS doit être dans le body POST, trouvé: {posted_data}"
        )
        assert 'type=STOP_LOSS_LIMIT' not in posted_data, (
            "C-02: STOP_LOSS_LIMIT ne doit plus apparaître dans le body POST"
        )

    @patch('exchange_client._api_rate_limiter')
    def test_no_time_in_force_param(self, mock_limiter):
        """C-02: timeInForce ne doit pas être envoyé avec STOP_LOSS."""
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 112, 'status': 'NEW'}

        with patch('exchange_client.requests.post', return_value=mock_resp) as mock_post:
            place_exchange_stop_loss(client, 'BTCUSDC', '0.001', 50000.0)

        posted_data = mock_post.call_args[1].get('data', '')
        assert 'timeInForce' not in posted_data, (
            "C-02: timeInForce ne doit pas être envoyé avec STOP_LOSS"
        )

    @patch('exchange_client._api_rate_limiter')
    def test_no_price_param_only_stop_price(self, mock_limiter):
        """C-02: seul stopPrice doit être envoyé (pas price)."""
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 113, 'status': 'NEW'}

        with patch('exchange_client.requests.post', return_value=mock_resp) as mock_post:
            place_exchange_stop_loss(client, 'BTCUSDC', '0.001', 50000.0)

        posted_data = mock_post.call_args[1].get('data', '')
        assert 'stopPrice=' in posted_data, "stopPrice doit être dans la requête"
        # Vérifier que 'price=' n'apparaît pas comme paramètre séparé
        # (il peut apparaître dans stopPrice, donc on vérifie spécifiquement)
        params_list = posted_data.split('&')
        price_params = [p for p in params_list if p.startswith('price=')]
        assert len(price_params) == 0, (
            f"C-02: paramètre 'price' ne doit pas être envoyé, trouvé: {price_params}"
        )

    @patch('exchange_client._api_rate_limiter')
    def test_stop_price_snapped_to_tick_size(self, mock_limiter):
        """Le stop_price doit être arrondi au tickSize du symbole."""
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 114, 'status': 'NEW'}

        # Envoyer un stop_price non arrondi : 50123.456 avec tick 0.01
        with patch('exchange_client.requests.post', return_value=mock_resp) as mock_post:
            place_exchange_stop_loss(client, 'BTCUSDC', '0.001', 50123.456)

        posted_data = mock_post.call_args[1].get('data', '')
        # Doit être arrondi à 50123.45 (tick 0.01)
        assert 'stopPrice=50123.45' in posted_data, (
            f"stopPrice doit être snappé au tickSize, trouvé: {posted_data}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2.  place_exchange_stop_loss — alertes email
# ══════════════════════════════════════════════════════════════════════════════

class TestPlaceExchangeStopLossAlerts:
    """Vérifie les alertes email sur succès/échec du placement stop-loss."""

    @patch('exchange_client._api_rate_limiter')
    def test_send_alert_called_on_success(self, mock_limiter):
        """L'alerte email doit être envoyée quand le SL est placé avec succès."""
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()
        mock_alert = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 200, 'status': 'NEW'}

        with patch('exchange_client.requests.post', return_value=mock_resp):
            place_exchange_stop_loss(
                client, 'SOLUSDC', '5.0', 95.0, send_alert=mock_alert
            )

        mock_alert.assert_called_once()
        call_kwargs = mock_alert.call_args[1]
        assert 'Stop-loss placé' in call_kwargs['subject']
        assert 'STOP_LOSS' in call_kwargs['body_main']

    @patch('exchange_client._api_rate_limiter')
    def test_send_alert_called_on_failure(self, mock_limiter):
        """L'alerte email doit être envoyée quand le SL échoue (erreur API)."""
        from exchange_client import place_exchange_stop_loss
        from exceptions import OrderError
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()
        mock_alert = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {'code': -2010, 'msg': 'Insufficient balance'}

        with patch('exchange_client.requests.post', return_value=mock_resp):
            with pytest.raises(OrderError):
                place_exchange_stop_loss(
                    client, 'SOLUSDC', '5.0', 95.0, send_alert=mock_alert
                )

        mock_alert.assert_called_once()
        call_kwargs = mock_alert.call_args[1]
        assert 'ERREUR STOP-LOSS' in call_kwargs['subject']
        assert '-2010' in call_kwargs['body_main']

    @patch('exchange_client._api_rate_limiter')
    def test_no_alert_when_send_alert_is_none(self, mock_limiter):
        """Pas d'erreur si send_alert est None."""
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 201, 'status': 'NEW'}

        with patch('exchange_client.requests.post', return_value=mock_resp):
            result = place_exchange_stop_loss(
                client, 'SOLUSDC', '5.0', 95.0, send_alert=None
            )
        assert result['orderId'] == 201


# ══════════════════════════════════════════════════════════════════════════════
# 3.  place_stop_loss_order — via create_order
# ══════════════════════════════════════════════════════════════════════════════

class TestPlaceStopLossOrder:
    """Tests pour place_stop_loss_order (utilise client.create_order)."""

    def test_success_returns_order_dict(self):
        """Cas nominal : l'ordre est placé avec succès."""
        from exchange_client import place_stop_loss_order
        client = _make_mock_client()
        client.create_order.return_value = {
            'orderId': 300, 'status': 'NEW', 'type': 'STOP_LOSS'
        }

        result = place_stop_loss_order(client, 'BTCUSDC', 0.001, 50000.0)
        assert result['orderId'] == 300
        client.create_order.assert_called_once()
        call_kwargs = client.create_order.call_args[1]
        assert call_kwargs['type'] == 'STOP_LOSS'
        assert call_kwargs['side'] == 'SELL'

    def test_sends_alert_on_success(self):
        """L'alerte email est envoyée sur succès."""
        from exchange_client import place_stop_loss_order
        client = _make_mock_client()
        mock_alert = MagicMock()
        client.create_order.return_value = {
            'orderId': 301, 'status': 'NEW'
        }

        place_stop_loss_order(client, 'BTCUSDC', 0.001, 50000.0,
                              send_alert=mock_alert)
        mock_alert.assert_called_once()
        assert 'STOP LOSS EXECUTE' in mock_alert.call_args[1]['subject']

    def test_sends_alert_on_failure(self):
        """L'alerte email est envoyée quand create_order lève BinanceAPIException."""
        from exchange_client import place_stop_loss_order
        from binance.exceptions import BinanceAPIException
        client = _make_mock_client()
        mock_alert = MagicMock()

        # Simuler une BinanceAPIException
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"code": -2010, "msg": "Insufficient balance"}'
        exc = BinanceAPIException(mock_response, 400, '{"code": -2010, "msg": "Insufficient balance"}')
        client.create_order.side_effect = exc

        # place_stop_loss_order est décorée @log_exceptions(default_return=None)
        # donc elle retourne None au lieu de lever l'exception
        result = place_stop_loss_order(client, 'BTCUSDC', 0.001, 50000.0,
                                        send_alert=mock_alert)
        # L'alerte d'erreur doit avoir été envoyée
        mock_alert.assert_called_once()
        assert 'ERREUR' in mock_alert.call_args[1]['subject']

    def test_uses_client_order_id_when_provided(self):
        """Le clientOrderId est passé à create_order."""
        from exchange_client import place_stop_loss_order
        client = _make_mock_client()
        client.create_order.return_value = {'orderId': 302, 'status': 'NEW'}

        place_stop_loss_order(client, 'BTCUSDC', 0.001, 50000.0,
                              client_id='custom-id-123')
        call_kwargs = client.create_order.call_args[1]
        assert call_kwargs['newClientOrderId'] == 'custom-id-123'


# ══════════════════════════════════════════════════════════════════════════════
# 4.  _direct_market_order — alertes email
# ══════════════════════════════════════════════════════════════════════════════

class TestDirectMarketOrderAlerts:
    """Vérifie les alertes email dans _direct_market_order."""

    def test_alert_on_success(self):
        """L'alerte email est envoyée après un ordre réussi."""
        from exchange_client import _direct_market_order
        client = _make_mock_client()
        mock_alert = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 400, 'status': 'FILLED'}

        with patch('exchange_client.requests.post', return_value=mock_resp):
            _direct_market_order(
                client, 'BTCUSDC', 'BUY',
                quoteOrderQty=100.0, send_alert=mock_alert
            )

        mock_alert.assert_called_once()
        assert 'BUY ORDER EXECUTE' in mock_alert.call_args[1]['subject']

    def test_alert_on_api_error(self):
        """L'alerte email est envoyée sur erreur API."""
        from exchange_client import _direct_market_order
        from exceptions import OrderError
        client = _make_mock_client()
        mock_alert = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {'code': -1013, 'msg': 'Filter failure'}

        with patch('exchange_client.requests.post', return_value=mock_resp):
            with pytest.raises(OrderError):
                _direct_market_order(
                    client, 'BTCUSDC', 'BUY',
                    quoteOrderQty=100.0, send_alert=mock_alert
                )

        mock_alert.assert_called_once()
        assert 'ERREUR' in mock_alert.call_args[1]['subject']

    def test_alert_on_exception(self):
        """L'alerte email est envoyée sur exception réseau."""
        from exchange_client import _direct_market_order
        from exceptions import OrderError
        client = _make_mock_client()
        mock_alert = MagicMock()

        with patch('exchange_client.requests.post', side_effect=ConnectionError("timeout")):
            with pytest.raises(OrderError):
                _direct_market_order(
                    client, 'BTCUSDC', 'SELL',
                    quantity=1.0, send_alert=mock_alert
                )

        mock_alert.assert_called_once()
        assert 'EXCEPTION' in mock_alert.call_args[1]['subject']


# ══════════════════════════════════════════════════════════════════════════════
# 5.  BinanceFinalClient — close_connection guard
# ══════════════════════════════════════════════════════════════════════════════

class TestBinanceFinalClientCloseGuard:
    """Teste que close_connection ne plante pas si session n'existe pas."""

    def test_close_without_session_attr(self):
        """close_connection ne doit pas lever AttributeError."""
        from exchange_client import BinanceFinalClient
        # Créer un objet brut sans __init__
        obj = object.__new__(BinanceFinalClient)
        # Pas de session attr — ne doit pas planter
        obj.close_connection()  # doit passer sans erreur

    def test_close_with_session(self):
        """close_connection appelle session.close() si elle existe."""
        from exchange_client import BinanceFinalClient
        obj = object.__new__(BinanceFinalClient)
        mock_session = MagicMock()
        obj.session = mock_session
        obj.close_connection()
        mock_session.close.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# 6.  place_exchange_stop_loss — erreur métier avec message STOP_LOSS
# ══════════════════════════════════════════════════════════════════════════════

class TestStopLossErrorMessages:
    """Vérifie que les messages d'erreur mentionnent STOP_LOSS (pas STOP_LOSS_LIMIT)."""

    @patch('exchange_client._api_rate_limiter')
    def test_error_message_contains_stop_loss(self, mock_limiter):
        """Le message OrderError doit contenir 'STOP_LOSS' (post C-02)."""
        from exchange_client import place_exchange_stop_loss
        from exceptions import OrderError
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {'code': -1013, 'msg': 'Filter failure'}

        with patch('exchange_client.requests.post', return_value=mock_resp):
            with pytest.raises(OrderError, match='STOP_LOSS') as exc_info:
                place_exchange_stop_loss(client, 'BTCUSDC', '0.001', 50000.0)
        # Vérifier que STOP_LOSS_LIMIT n'apparaît pas dans le message
        assert 'STOP_LOSS_LIMIT' not in str(exc_info.value)

    @patch('exchange_client._api_rate_limiter')
    @patch('exchange_client.time.sleep', return_value=None)
    @patch('exchange_client.random.uniform', return_value=0.0)
    def test_exhaustion_error_message(self, mock_rand, mock_sleep, mock_limiter):
        """Après épuisement des retries, le message doit mentionner le symbole."""
        from exchange_client import place_exchange_stop_loss
        from exceptions import OrderError
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()

        with patch('exchange_client.requests.post', side_effect=ConnectionError("down")):
            with pytest.raises(OrderError, match='BTCUSDC'):
                place_exchange_stop_loss(client, 'BTCUSDC', '0.001', 50000.0)


# ══════════════════════════════════════════════════════════════════════════════
# 7.  place_exchange_stop_loss — tick-size edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestStopLossTickSizeEdge:
    """Cas limites pour le snapping du stop_price au tickSize."""

    @patch('exchange_client._api_rate_limiter')
    def test_fallback_tick_size_on_missing_symbol_info(self, mock_limiter):
        """Si get_symbol_info retourne None, utilise tick=0.01 par défaut."""
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()
        client.get_symbol_info.return_value = None  # pas d'info symbole

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 500, 'status': 'NEW'}

        with patch('exchange_client.requests.post', return_value=mock_resp) as mock_post:
            result = place_exchange_stop_loss(client, 'NEWUSDC', '1.0', 99.999)

        assert result['orderId'] == 500
        posted_data = mock_post.call_args[1].get('data', '')
        # Avec tick=0.01, 99.999 → 99.99
        assert 'stopPrice=99.99' in posted_data

    @patch('exchange_client._api_rate_limiter')
    def test_fallback_tick_size_on_exception(self, mock_limiter):
        """Si get_symbol_info lève une exception, utilise tick=0.01."""
        from exchange_client import place_exchange_stop_loss
        mock_limiter.acquire.return_value = True
        client = _make_mock_client()
        client.get_symbol_info.side_effect = Exception("API down")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'orderId': 501, 'status': 'NEW'}

        with patch('exchange_client.requests.post', return_value=mock_resp):
            result = place_exchange_stop_loss(client, 'NEWUSDC', '1.0', 99.999)
        assert result['orderId'] == 501


# ══════════════════════════════════════════════════════════════════════════════
# 8.  _generate_client_order_id — format et unicité
# ══════════════════════════════════════════════════════════════════════════════

class TestGenerateClientOrderIdFormat:
    """Tests supplémentaires sur le format du client order ID."""

    def test_sl_prefix(self):
        """Le prefix 'sl' doit apparaître dans l'ID généré."""
        from exchange_client import _generate_client_order_id
        cid = _generate_client_order_id('sl')
        assert cid.startswith('sl-')

    def test_buy_prefix(self):
        from exchange_client import _generate_client_order_id
        cid = _generate_client_order_id('buy')
        assert cid.startswith('buy-')

    def test_contains_timestamp_and_uuid(self):
        """L'ID contient un timestamp et un fragment UUID."""
        from exchange_client import _generate_client_order_id
        cid = _generate_client_order_id('bot')
        parts = cid.split('-')
        assert len(parts) == 3  # prefix-timestamp-uuid8
        assert len(parts[2]) == 8  # 8 hex chars from uuid4
