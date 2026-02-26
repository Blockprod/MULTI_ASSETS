#!/usr/bin/env python3
"""
 BIBOT TRADING SYSTEM - SETUP AUTOMATIQUE
Configuration optimisée des dépendances
"""

import subprocess
import sys
import os
from pathlib import Path

def run_command(cmd, description):
    """Exécute une commande avec gestion d'erreur"""
    print(f"\n {description}...")
    try:
        result = subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        print(f" {description} - SUCCESS")
        return True
    except subprocess.CalledProcessError as e:
        print(f" {description} - FAILED: {e.stderr}")
        return False

def setup_trading_environment():
    """Configuration complète de l'environnement"""
    
    print(" BIBOT TRADING SYSTEM - SETUP AUTOMATIQUE")
    print("=" * 60)
    
    # Vérification Python
    python_version = sys.version_info
    if python_version.major != 3 or python_version.minor < 8:
        print(" Python 3.8+ requis")
        return False
    
    print(f" Python {python_version.major}.{python_version.minor}.{python_version.micro}")
    
    # Mise à jour pip
    run_command(f"{sys.executable} -m pip install --upgrade pip", "Mise à jour pip")
    
    # Installation par blocs compatibles
    install_commands = [
        # Core
        f"{sys.executable} -m pip install pandas==2.1.4 numpy==1.24.3 scipy==1.11.4",
        
        # ML Ecosystem
        f"{sys.executable} -m pip install scikit-learn==1.3.2 xgboost==2.0.3 lightgbm==4.1.0",
        
        # Performance
        f"{sys.executable} -m pip install numba==0.58.1 joblib==1.3.2",
        
        # Trading
        f"{sys.executable} -m pip install MetaTrader5==5.0.45",
        
        # Visualization
        f"{sys.executable} -m pip install matplotlib==3.8.2 seaborn==0.13.0",
        
        # Utilities
        f"{sys.executable} -m pip install tqdm==4.66.1 python-dateutil==2.8.2 pytz==2023.3"
    ]
    
    success_count = 0
    for cmd in install_commands:
        if run_command(cmd, f"Installation bloc {success_count + 1}"):
            success_count += 1
    
    # TA-Lib (installation spéciale)
    print("\n Installation TA-Lib...")
    try:
        import talib
        print(" TA-Lib déjà installé")
    except ImportError:
        print("  TA-Lib non trouvé - Installation manuelle requise")
        print("   Téléchargez: https://www.lfd.uci.edu/~gohlke/pythonlibs/#ta-lib")
    
    # Test des imports critiques
    print("\n TEST DES IMPORTS CRITIQUES:")
    critical_imports = [
        ('pandas', 'pd'),
        ('numpy', 'np'),
        ('sklearn', 'sklearn'),
        ('xgboost', 'xgb'),
        ('lightgbm', 'lgb'),
        ('MetaTrader5', 'mt5'),
        ('matplotlib.pyplot', 'plt'),
        ('numba', 'numba')
    ]
    
    for module, alias in critical_imports:
        try:
            exec(f"import {module} as {alias}")
            print(f" {module}")
        except ImportError as e:
            print(f" {module}: {e}")
    
    print("\n SETUP TERMINÉ!")
    print(f" {success_count}/{len(install_commands)} blocs installés")
    
    return True

if __name__ == "__main__":
    setup_trading_environment()