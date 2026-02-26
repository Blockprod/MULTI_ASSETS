from setuptools import setup, Extension
import numpy as np
from Cython.Build import cythonize

# Extensions à compiler
extensions = [
    Extension(
        "indicators",
        ["indicators.pyx"],
        include_dirs=[np.get_include()],
        language="c++",
    ),
    Extension(
        "backtest_engine",
        ["backtest_engine.pyx"],
        include_dirs=[np.get_include()],
        language="c++",
    ),
    Extension(
        "backtest_engine_standard",
        ["backtest_engine_standard.pyx"],
        include_dirs=[np.get_include()],
        language="c++",
    ),
]

# Compilation Cython
ext_modules = cythonize(
    extensions,
    compiler_directives={
        'language_level': "3",
        'boundscheck': False,
        'wraparound': False,
        'cdivision': True,
    },
    annotate=False,
)

setup(
    name="crypto_trading_fast",
    version="1.0.0",
    description="Modules Cython optimisés pour trading crypto",
    ext_modules=ext_modules,
    include_dirs=[np.get_include()],
    zip_safe=False,
)
