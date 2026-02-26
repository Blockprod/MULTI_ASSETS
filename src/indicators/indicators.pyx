# cython: language_level=3
# distutils: language = c++
# cython: boundscheck=False, wraparound=False, cdivision=True

import numpy as np

cimport cython
cimport numpy as np

import pandas as pd

from libc.math cimport abs

DTYPE = np.float64
ctypedef np.float64_t DTYPE_t

# ...existing code...

# Fonction de test minimale pour valider la compilation Cython
def test_hello():
	print("Hello from indicators!")
