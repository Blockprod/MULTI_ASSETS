#!/usr/bin/env python3
"""Script de vérification des protections anti-mismatch."""

import re

def verify_protections():
    """Vérifie que toutes les protections sont en place dans MULTI_SYMBOLS.py"""
    
    with open('MULTI_SYMBOLS.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    checks = {
        "Log execute_real_trades START": r'\[execute_real_trades\] START',
        "Garde-fou SCENARIO MISMATCH": r'SCENARIO MISMATCH DETECTED',
        "Run ID generation": r'run_id = f"RUN-',
        "Strategy snapshot": r'strategy_snapshot = json.dumps',
        "Run ID in failure email": r'Run Id\s*:.*\{run_id\}',
        "Snapshot in failure email": r'Strategie snapshot\s*:.*\{strategy_snapshot\}',
    }
    
    print("=" * 70)
    print("VERIFICATION DES PROTECTIONS ANTI-MISMATCH")
    print("=" * 70)
    print()
    
    all_ok = True
    for name, pattern in checks.items():
        found = re.search(pattern, content)
        status = " OK" if found else " MANQUANT"
        color = "\033[92m" if found else "\033[91m"
        reset = "\033[0m"
        print(f"{color}{status}{reset} - {name}")
        if not found:
            all_ok = False
    
    print()
    print("=" * 70)
    if all_ok:
        print("\033[92m TOUTES LES PROTECTIONS SONT EN PLACE\033[0m")
    else:
        print("\033[91m CERTAINES PROTECTIONS MANQUENT\033[0m")
    print("=" * 70)
    
    return all_ok

if __name__ == "__main__":
    verify_protections()
