# cython: language_level=3
# distutils: language = c++
# Moteur de backtest optimisé en Cython pour 30-50x accélération

import numpy as np

cimport cython
cimport numpy as np

import pandas as pd

from libc.math cimport abs, fmax, fmin
from libc.stdlib cimport free, malloc

DTYPE = np.float64
ctypedef np.float64_t DTYPE_t

# ...existing code...


# Fonction de test minimale pour valider la compilation Cython
def test_hello():
	print("Hello from backtest_engine!")

# --- Implémentation EMA rapide en Cython ---
@cython.boundscheck(False)
@cython.wraparound(False)
def calculate_ema_fast(np.ndarray[DTYPE_t, ndim=1] arr, int period):
	cdef int n = arr.shape[0]
	cdef np.ndarray[DTYPE_t, ndim=1] ema = np.empty(n, dtype=DTYPE)
	cdef double alpha = 2.0 / (period + 1)
	cdef int i
	if n == 0:
		return np.array([])
	ema[0] = arr[0]
	for i in range(1, n):
		ema[i] = alpha * arr[i] + (1 - alpha) * ema[i-1]
	return ema
