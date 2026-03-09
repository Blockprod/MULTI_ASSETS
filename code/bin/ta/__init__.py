"""Proxy shim — remplace code/bin/ta par le vrai paquet ta du venv à l'exécution.

Existe uniquement pour que Pylance (extraPaths) reconnaisse les sous-modules
`ta.momentum`, `ta.trend`, `ta.volatility` comme importables.  Au runtime,
ce module se remplace immédiatement par le vrai paquet installé dans le venv.
"""
import sys as _sys
import importlib as _il
import os.path as _op

_this_dir = _op.dirname(_op.abspath(__file__))   # .../code/bin/ta
_bin_dir = _op.dirname(_this_dir)                # .../code/bin

# Retirer code/bin (et code/bin/ta) du sys.path pour trouver le vrai ta
_removed: list = []
for _p in list(_sys.path):
    if _op.normpath(_p) in (_op.normpath(_bin_dir), _op.normpath(_this_dir)):
        _sys.path.remove(_p)
        _removed.append(_p)

try:
    # Purger toutes les entrées ta.* du cache (proxy partiel en cours de chargement)
    for _k in [k for k in list(_sys.modules) if k == 'ta' or k.startswith('ta.')]:
        del _sys.modules[_k]
    # Importer le vrai ta depuis le venv (code/bin absent du sys.path)
    _real_ta = _il.import_module('ta')
    # Remplacer ce proxy dans sys.modules par le vrai paquet
    _sys.modules[__name__] = _real_ta
finally:
    # Restaurer sys.path dans le même ordre
    for _i, _p in enumerate(_removed):
        _sys.path.insert(_i, _p)

del _sys, _il, _op, _this_dir, _bin_dir, _removed, _real_ta
