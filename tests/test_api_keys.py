#!/usr/bin/env python3
"""
Script de test pour vérifier les clés API Binance
"""

import os
from dotenv import load_dotenv
import requests
import time
import hmac
import hashlib
from urllib.parse import urlencode

def test_api_keys():
    """Test simple des clés API Binance"""
    
    # Charger les variables d'environnement
    load_dotenv()
    
    api_key = os.getenv('BINANCE_API_KEY')
    api_secret = os.getenv('BINANCE_SECRET_KEY')
    
    print(f"API Key: {api_key[:10] if api_key else 'VIDE'}...")
    print(f"API Secret: {'OK' if api_secret and len(api_secret) > 10 else 'PROBLEME'} (longueur: {len(api_secret) if api_secret else 0})")
    
    if not api_key or not api_secret:
        print("ERREUR: Cles API manquantes dans le fichier .env")
        assert False, "Clés API manquantes dans le fichier .env"
    
    # Test 1: Ping (pas de signature requise)
    print("\nTest 1: Ping API...")
    try:
        response = requests.get("https://api.binance.com/api/v3/ping", timeout=10)
        if response.status_code == 200:
            print("Ping reussi")
        else:
            print(f"Ping echoue: {response.status_code}")
            assert False, f"Ping échoué: {response.status_code}"
    except Exception as e:
        print(f"Erreur ping: {e}")
        assert False, f"Erreur ping: {e}"
    
    # Test 2: Temps serveur (pas de signature requise)
    print("\nTest 2: Temps serveur...")
    try:
        response = requests.get("https://api.binance.com/api/v3/time", timeout=10)
        if response.status_code == 200:
            server_time = response.json()['serverTime']
            local_time = int(time.time() * 1000)
            diff = server_time - local_time
            print(f"Temps serveur recupere")
            print(f"   Difference: {diff} ms")
        else:
            print(f"Temps serveur echoue: {response.status_code}")
            assert False, f"Temps serveur échoué: {response.status_code}"
    except Exception as e:
        print(f"Erreur temps serveur: {e}")
        assert False, f"Erreur temps serveur: {e}"
    
    # Test 3: Requête signée (account info)
    print("\nTest 3: Requete signee (account info)...")
    try:
        # Paramètres
        timestamp = int(time.time() * 1000) - 5000  # 5 secondes de retard
        params = {
            'timestamp': timestamp,
            'recvWindow': 60000
        }
        
        # Créer la signature
        query_string = urlencode(params)
        signature = hmac.new(
            api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        params['signature'] = signature
        
        # Headers
        headers = {
            'X-MBX-APIKEY': api_key
        }
        
        print(f"   Query string: {query_string}")
        print(f"   Signature: {signature[:20]}...")
        
        # Requête
        response = requests.get(
            "https://api.binance.com/api/v3/account",
            params=params,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            account_data = response.json()
            print("Requete signee reussie")
            print(f"   Compte type: {account_data.get('accountType', 'N/A')}")
            print(f"   Permissions: {account_data.get('permissions', [])}")
        else:
            print(f"Requete signee echouee: {response.status_code}")
            print(f"   Reponse: {response.text}")
            assert False, f"Requête signée échouée: {response.status_code} — {response.text}"
            
    except Exception as e:
        print(f"Erreur requete signee: {e}")
        assert False, f"Erreur requête signée: {e}"

if __name__ == "__main__":
    print("Test des cles API Binance")
    print("=" * 50)
    
    try:
        test_api_keys()
        print("\n" + "=" * 50)
        print("TOUS LES TESTS REUSSIS - Cles API fonctionnelles")
    except AssertionError as e:
        print("\n" + "=" * 50)
        print(f"ECHEC DES TESTS - {e}")