import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

import json
from datetime import datetime
from LTV_check_improved import get_binance_price

def reset_portfolio_reference():
    """Remet à zéro les prix de référence avec les prix actuels"""
    print(" RESET PORTFOLIO - Nouveaux prix de référence")
    
    # Récupération des prix actuels
    price_eth = get_binance_price("ETHUSDT")
    price_xrp = get_binance_price("XRPUSDT")
    price_hbar = get_binance_price("HBARUSDT")
    
    if not all([price_eth, price_xrp, price_hbar]):
        print(" Impossible de récupérer les prix actuels")
        return
    
    # Sauvegarde des nouveaux prix de référence
    reference_data = {
        'date_creation': datetime.now().isoformat(),
        'eth_reference_price': price_eth,
        'xrp_reference_price': price_xrp,
        'hbar_reference_price': price_hbar,
        'note': 'Prix de référence pour calcul PnL - Reset manuel'
    }
    
    with open('portfolio_reference.json', 'w') as f:
        json.dump(reference_data, f, indent=2)
    
    print(" Nouveaux prix de référence enregistrés:")
    print(f"  ETH: ${price_eth:.2f}")
    print(f"  XRP: ${price_xrp:.4f}")
    print(f"  HBAR: ${price_hbar:.4f}")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n Le PnL sera maintenant calculé à partir de ces nouveaux prix de référence")

if __name__ == '__main__':
    reset_portfolio_reference()