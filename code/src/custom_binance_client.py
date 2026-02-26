import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

import requests
import time
import hmac
import hashlib
from urllib.parse import urlencode
import logging

logger = logging.getLogger(__name__)

class CustomBinanceClient:
    """Client Binance 100% personnalisé avec gestion absolue des timestamps."""
    
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.binance.com"
        self.session = requests.Session()
        self.session.headers.update({'X-MBX-APIKEY': api_key})
        
    def _get_server_time(self):
        """Récupère le temps serveur Binance."""
        try:
            response = self.session.get(f"{self.base_url}/api/v3/time", timeout=10)
            return response.json()['serverTime']
        except Exception as e:
            logger.error(f"Erreur récupération temps serveur: {e}")
            return int(time.time() * 1000)
    
    def _create_signature(self, params):
        """Crée la signature HMAC pour l'authentification."""
        # Créer la query string avec les paramètres triés (sans la signature)
        params_copy = {k: v for k, v in params.items() if k != 'signature'}
        query_string = urlencode(sorted(params_copy.items()))
        
        logger.debug(f"Query string pour signature: {query_string}")
        logger.debug(f"Clé API: {self.api_key[:10]}...")
        logger.debug(f"Clé secrète (longueur): {len(self.api_secret)} caractères")
        
        # Vérifier que la clé secrète n'est pas vide
        if not self.api_secret:
            raise ValueError("Clé secrète vide")
        
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        logger.debug(f"Signature générée: {signature[:20]}...")
        return signature
    
    def _signed_request(self, method, endpoint, params=None):
        """Effectue une requête signée avec timestamp ultra-sécurisé."""
        if params is None:
            params = {}
        
        # FORCER un timestamp sécurisé (serveur - 3 secondes)
        server_time = self._get_server_time()
        safe_timestamp = server_time - 3000
        params['timestamp'] = safe_timestamp
        
        # Ajouter une fenêtre de réception élargie (60 secondes) **seulement si absente**
        if 'recvWindow' not in params:
            params['recvWindow'] = 60000
        
        # Créer la signature
        params['signature'] = self._create_signature(params)
        
        url = f"{self.base_url}{endpoint}"
        
        logger.debug(f"Requête signée: {method} {endpoint} avec timestamp {safe_timestamp} et recvWindow 60s")
        
        if method.upper() == 'GET':
            response = self.session.get(url, params=params, timeout=30)
        elif method.upper() == 'POST':
            response = self.session.post(url, data=params, timeout=30)
        else:
            raise ValueError(f"Méthode HTTP non supportée: {method}")
        
        if response.status_code != 200:
            logger.error(f"Erreur API: {response.status_code} - {response.text}")
            response.raise_for_status()
        
        return response.json()
    
    def ping(self):
        """Test de connectivité."""
        response = self.session.get(f"{self.base_url}/api/v3/ping", timeout=10)
        return response.status_code == 200
    
    def get_server_time(self):
        """Récupère le temps serveur."""
        response = self.session.get(f"{self.base_url}/api/v3/time", timeout=10)
        return response.json()
    
    def get_symbol_ticker(self, symbol):
        """Récupère le prix d'un symbole."""
        params = {'symbol': symbol}
        response = self.session.get(f"{self.base_url}/api/v3/ticker/price", params=params, timeout=10)
        return response.json()
    
    def get_exchange_info(self):
        """Récupère les informations d'échange."""
        response = self.session.get(f"{self.base_url}/api/v3/exchangeInfo", timeout=10)
        return response.json()
    
    def get_symbol_info(self, symbol):
        """Récupère les informations d'un symbole."""
        exchange_info = self.get_exchange_info()
        for s in exchange_info['symbols']:
            if s['symbol'] == symbol:
                return s
        return None
    
    def get_account(self):
        """Récupère les informations du compte."""
        return self._signed_request('GET', '/api/v3/account')
    
    def get_asset_balance(self, asset):
        """Récupère le solde d'un actif."""
        account = self.get_account()
        for balance in account['balances']:
            if balance['asset'] == asset:
                return balance
        return None
    
    def get_all_orders(self, symbol, limit=500):
        """Récupère tous les ordres."""
        params = {'symbol': symbol, 'limit': limit}
        return self._signed_request('GET', '/api/v3/allOrders', params)
    
    def get_my_trades(self, symbol, limit=500):
        """Récupère les trades."""
        params = {'symbol': symbol, 'limit': limit}
        return self._signed_request('GET', '/api/v3/myTrades', params)
    
    def order_market_buy(self, symbol, quoteOrderQty):
        """Place un ordre d'achat au marché."""
        params = {
            'symbol': symbol,
            'side': 'BUY',
            'type': 'MARKET',
            'quoteOrderQty': str(quoteOrderQty)
        }
        return self._signed_request('POST', '/api/v3/order', params)
    
    def order_market_sell(self, symbol, quantity):
        """Place un ordre de vente au marché."""
        params = {
            'symbol': symbol,
            'side': 'SELL',
            'type': 'MARKET',
            'quantity': str(quantity)
        }
        return self._signed_request('POST', '/api/v3/order', params)
    
    def get_historical_klines(self, symbol, interval, start_str, limit=1000):
        """Récupère les données historiques."""
        params = {
            'symbol': symbol,
            'interval': interval,
            'startTime': self._parse_start_time(start_str),
            'limit': limit
        }
        
        all_klines = []
        while True:
            response = self.session.get(f"{self.base_url}/api/v3/klines", params=params, timeout=30)
            klines = response.json()
            
            if not klines:
                break
                
            all_klines.extend(klines)
            
            # Préparer pour la prochaine requête
            params['startTime'] = klines[-1][6] + 1  # Close time + 1ms
            
            if len(klines) < limit:
                break
        
        return all_klines
    
    def _parse_start_time(self, start_str):
        """Parse la date de début."""
        from datetime import datetime
        try:
            dt = datetime.strptime(start_str, "%d %B %Y")
            return int(dt.timestamp() * 1000)
        except:
            # Fallback: 1 an en arrière
            return int((time.time() - 365*24*3600) * 1000)