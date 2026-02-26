import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

from flask import Flask, render_template, jsonify
import json
from datetime import datetime

from LTV_check_improved import *
from analyze_ltv_strategy import analyze_ltv_strategy

app = Flask(__name__)

def get_dashboard_data():
    """Récupère toutes les données pour le dashboard"""
    # Récupération des prix et volatilité
    price_eth = get_binance_price("ETHUSDT")
    price_xrp = get_binance_price("XRPUSDT")
    price_hbar = get_binance_price("HBARUSDT")
    
    volatility_data = {
        'eth': get_binance_volatility("ETHUSDT"),
        'xrp': get_binance_volatility("XRPUSDT"), 
        'hbar': get_binance_volatility("HBARUSDT")
    }
    
    if not all([price_eth, price_xrp, price_hbar]):
        return None
    
    # Paramètres
    eth_collateral = 0.272
    xrp_collateral = 398.3
    ltv_eth = 0.22
    ltv_xrp = 0.20
    apr_eth = 0.0137
    apr_xrp = 0.0125
    apr_hbar = 0.0016
    
    # Calculs
    collateral_eth_usd = eth_collateral * price_eth
    borrow_eth_usd = collateral_eth_usd * ltv_eth
    borrow_eth_hbar = borrow_eth_usd / price_hbar
    
    collateral_xrp_usd = xrp_collateral * price_xrp
    borrow_xrp_usd = collateral_xrp_usd * ltv_xrp
    borrow_xrp_hbar = borrow_xrp_usd / price_hbar
    
    eth_ltv_current = borrow_eth_usd / collateral_eth_usd
    xrp_ltv_current = borrow_xrp_usd / collateral_xrp_usd
    
    total_borrow_hbar = borrow_eth_hbar + borrow_xrp_hbar
    eth_earn_usd = collateral_eth_usd * (apr_eth / 365)
    xrp_earn_usd = collateral_xrp_usd * (apr_xrp / 365)
    hbar_earn_usd = total_borrow_hbar * price_hbar * (apr_hbar / 365)
    total_earn_usd = eth_earn_usd + xrp_earn_usd + hbar_earn_usd
    
    strategy_data = {
        'timestamp': datetime.now().isoformat(),
        'price_eth': price_eth,
        'price_xrp': price_xrp,
        'price_hbar': price_hbar,
        'eth_ltv_current': eth_ltv_current,
        'eth_ltv_max': ltv_eth,
        'xrp_ltv_current': xrp_ltv_current,
        'xrp_ltv_max': ltv_xrp,
        'collateral_eth_usd': collateral_eth_usd,
        'collateral_xrp_usd': collateral_xrp_usd,
        'borrow_eth_hbar': borrow_eth_hbar,
        'borrow_xrp_hbar': borrow_xrp_hbar,
        'total_borrow_hbar': total_borrow_hbar,
        'total_earn_usd': total_earn_usd,
        'apr_eth': apr_eth,
        'apr_xrp': apr_xrp,
        'apr_hbar': apr_hbar
    }
    
    recommendations = analyze_ltv_strategy(strategy_data, volatility_data)
    
    return {
        'prices': {
            'eth': price_eth,
            'xrp': price_xrp,
            'hbar': price_hbar
        },
        'volatility': volatility_data,
        'positions': {
            'eth': {
                'collateral_usd': collateral_eth_usd,
                'ltv_current': eth_ltv_current,
                'ltv_target': ltv_eth,
                'borrow_hbar': borrow_eth_hbar,
                'earn_daily': eth_earn_usd
            },
            'xrp': {
                'collateral_usd': collateral_xrp_usd,
                'ltv_current': xrp_ltv_current,
                'ltv_target': ltv_xrp,
                'borrow_hbar': borrow_xrp_hbar,
                'earn_daily': xrp_earn_usd
            }
        },
        'totals': {
            'borrow_hbar': total_borrow_hbar,
            'earn_daily': total_earn_usd
        },
        'recommendations': recommendations,
        'high_volatility': max(volatility_data.values()) > 0.05,
        'last_update': datetime.now().strftime('%H:%M:%S')
    }

@app.route('/')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/data')
def api_data():
    data = get_dashboard_data()
    if data is None:
        return jsonify({'error': 'Impossible de récupérer les données'}), 500
    return jsonify(data)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)