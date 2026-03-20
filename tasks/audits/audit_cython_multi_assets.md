# Audit Cython — MULTI_ASSETS
**Date** : 2026-03-20  
**Auditeur** : GitHub Copilot  
**Commit de référence** : `60a0a0f`

---

## BLOC 1 — Inventaire des artefacts

| Module | `.pyx` (`code/`) | cp311 `.pyd` (`code/bin/`) | cp313 `.pyd` (`code/bin/`) | `.cpp` (`code/`) | `.pyi` (`code/bin/`) |
|---|:---:|:---:|:---:|:---:|:---:|
| `backtest_engine_standard` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `backtest_engine` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `indicators` | ✅ | ✅ | ✅ | ✅ | ✅ |

**Remarques :**
- Backend C++ (`language = 'c++'` dans `config/setup.py`) → artefacts intermédiaires `.cpp` (pas `.c`).  
- Les `.pyd` et `.so` sont dans `.gitignore` (lignes 16-17) → **les compilés ne sont pas commités** ; un `python config/setup.py build_ext --inplace` est requis après chaque clone ou changement Python.

---

## BLOC 2 — Cohérence interfaces (.pyx ↔ .pyi)

### `backtest_engine_standard`

**Signature `.pyx`** (source of truth) :

```python
def backtest_from_dataframe_fast(
    close, high, low, ema1, ema2, stoch, atr,
    sma_long=None, adx=None, trix_histo=None, open=None,
    volume=None, vol_sma=None,
    initial_wallet, scenario, use_sma, use_adx, use_trix,
    use_vol_filter, taker_fee, slippage_buy, slippage_sell,
    atr_multiplier, atr_stop_multiplier, stoch_threshold_buy,
    stoch_threshold_sell, adx_threshold, sizing_mode,
    risk_per_trade, partial_enabled, partial_threshold_1,
    partial_threshold_2, partial_pct_1, partial_pct_2,
    min_notional, stoch_threshold_buy_min, breakeven_enabled,
    breakeven_trigger_pct, cooldown_candles, mtf_bullish,
    use_mtf_filter
) -> dict
```

**Verdict :** `.pyi` CONFORME pour `backtest_from_dataframe_fast`.

> 🟠 **ÉCART — Stub orphelin** : `code/bin/backtest_engine_standard.pyi` déclare une fonction `calculate_indicators_fast()` qui **n'existe pas** dans `code/backtest_engine_standard.pyx`. Le stub est un fantôme — aucun appelant ne peut s'en servir via le compilé.

---

### `backtest_engine` (legacy)

**Signature `.pyx`** :

```python
def backtest_from_dataframe_fast(
    close, open, high, low, ema1, ema2, stoch, atr,
    hv=None, sma=None, adx=None, trix=None,
    initial_wallet, scenario, use_sma, use_adx, use_trix,
    atr_filter_multiplier
) -> dict
```

Fonctions vectorisées (public `def`, confirmées aux lignes 340, 357, 396, 436) :

```python
def vectorized_ema(prices: ndarray, period: int) -> ndarray
def vectorized_rsi(prices: ndarray) -> ndarray
def vectorized_atr(high: ndarray, low: ndarray, close: ndarray, period: int) -> ndarray
def vectorized_stoch_rsi(rsi: ndarray, period: int = 14) -> ndarray
```

**Verdict :** `.pyi` CONFORME — les 4 fonctions vectorisées sont bien des `def` publics.

> 🟠 **ÉCART ARCHITECTURAL — DEF compile-time constants** : `backtest_engine.pyx` utilise des constantes compilées (`DEF ATR_MULTIPLIER=5.5`, `DEF STOP_LOSS_ATR_MULT=2.0`, etc.) **non configurables at runtime**, contrairement à `backtest_engine_standard` qui accepte `atr_multiplier`, `atr_stop_multiplier` comme paramètres. Les deux moteurs ne sont donc **pas interchangeables**.

> 🟡 **Note style** : `backtest_engine.pyi` utilise `dict[str, object]` (style Python 3.10+) là où `backtest_engine_standard.pyi` utilise `Dict[str, Any]` (typing module). Cohérence de style inter-stubs absente — sans conséquence fonctionnelle.

---

### `indicators`

**Signature `.pyx`** :

```python
def calculate_indicators(
    df: pd.DataFrame,
    ema1_period: int,
    ema2_period: int,
    stoch_period: int = 14,
    sma_long: int = 0,
    adx_period: int = 0,
    trix_length: int = 0,
    trix_signal: int = 0
) -> pd.DataFrame
```

**Verdict :** `.pyi` CONFORME — aucun écart détecté.

---

## BLOC 3 — Imports runtime

**Fichier** : `code/src/backtest_runner.py`

| Check | Résultat |
|---|---|
| `_BIN_DIR` injecté dans `sys.path` | ✅ (lignes 91-92) |
| Module importé | `import backtest_engine_standard as backtest_engine` (ligne 97) |
| Seul module actif en production | ✅ `backtest_engine_standard` uniquement |
| `ImportError` capturé | ✅ `CYTHON_BACKTEST_AVAILABLE = False` |
| Fallback Python complet | ✅ (boucle Python pure, ligne 365+) |
| `backtest_engine` (legacy) importé | 🟡 Jamais — uniquement dans tests |
| Fallback cp311 → cp313 explicite | 🟡 Non — Python sélectionne automatiquement |

> 🟡 **`backtest_engine` (legacy) jamais utilisé en production** : le module est compilé (cp311 + cp313), maintenu dans `setup.py`, mais aucun appelant en dehors des tests. Risque de dérive silencieuse par rapport à `backtest_engine_standard` au fil des évolutions.

---

## BLOC 4 — Configuration build

**Fichier** : `config/setup.py`

| Check | Résultat |
|---|---|
| 3 extensions déclarées | ✅ (`indicators`, `backtest_engine`, `backtest_engine_standard`) |
| `language_level='3'` | ✅ via `compiler_directives` (global) + `# cython: language_level=3` en tête de chaque `.pyx` |
| `annotate=False` | ✅ (pas de fichiers `.html` générés) |
| `boundscheck=False` | ✅ via `compiler_directives` |
| `wraparound=False` | ✅ via `compiler_directives` |
| `cdivision=True` | ✅ via `compiler_directives` |
| Cython version pinnée | ✅ `Cython==3.2.4` dans `requirements.txt` |
| `.pyd` dans `.gitignore` | ✅ (lignes 16-17) |

**Remarque build** : la redondance `language_level` (global `compiler_directives` + directive en tête de `.pyx`) est inoffensive — le comportement est identique, elle sert de documentation inline.

---

## BLOC 5 — Couverture tests

| Module | Test dédié | Pattern |
|---|---|---|
| `backtest_engine_standard` | ✅ `test_p1_p2_fixes.py::TestCythonEngineP1P2` | `try/import` + `pytest.skip` si `.pyd` absent |
| `indicators` | ✅ `test_indicators_consistency.py::TestPythonCythonConsistency` | `try/import` + `.pyd`/`.so` vérifié |
| `backtest_engine` (legacy) | 🟡 Aucun test dédié | — |

**Pattern d'import dans les tests :** chaque test injecte `code/bin/` dans `sys.path` manuellement, vérifie `__file__.endswith('.pyd')` pour s'assurer que c'est le compilé (pas un stub Python), et appelle `pytest.skip` si le `.pyd` est absent. Aucun patching dans `conftest.py`.

> 🟡 **Paramètres `partial_enabled`, `breakeven_enabled`, `cooldown_candles`, `mtf_bullish`** de `backtest_engine_standard` — non couverts par les tests Cython actuels (uniquement testés via le chemin Python dans `backtest_runner.py`).

---

## Récapitulatif des écarts

| Sévérité | # | Description | Fichier(s) concerné(s) |
|---|---|---|---|
| 🟠 Majeur | 1 | `calculate_indicators_fast()` stub orphelin — déclaré dans `.pyi` mais absent du `.pyx` | `code/bin/backtest_engine_standard.pyi` |
| 🟠 Majeur | 2 | `backtest_engine` (legacy) jamais importé en production — compilé et maintenu sans usage live, risque de dérive | `code/backtest_engine.pyx`, `config/setup.py` |
| 🟠 Majeur | 3 | DEF compile-time constants dans `backtest_engine.pyx` (ATR_MULTIPLIER, STOP_LOSS_ATR_MULT…) — non configurables runtime, divergence architecturale avec standard | `code/backtest_engine.pyx` |
| 🟡 Mineur | 4 | `.pyd` gitignorés → build obligatoire après chaque clone (absent du README) | `.gitignore`, `README.md` |
| 🟡 Mineur | 5 | `backtest_engine` (legacy) sans test dédié | `tests/` |
| 🟡 Mineur | 6 | Paramètres optionnels avancés de `backtest_engine_standard` (`partial_*`, `breakeven_*`, `cooldown_candles`, `mtf_bullish`) non couverts par les tests Cython | `tests/test_p1_p2_fixes.py` |
| 🟡 Mineur | 7 | Style `.pyi` incohérent entre modules (`Dict[str, Any]` vs `dict[str, object]`) | `code/bin/backtest_engine*.pyi` |

**Total : 🔴 0 · 🟠 3 · 🟡 4**

---

## Actions recommandées (par priorité)

### Priorité 1 — Corriger le stub orphelin (🟠1)
Supprimer `calculate_indicators_fast` de `code/bin/backtest_engine_standard.pyi` ou implémenter la fonction dans le `.pyx` si elle est réellement nécessaire.

```diff
# code/bin/backtest_engine_standard.pyi
- def calculate_indicators_fast(...) -> ...: ...
```

### Priorité 2 — Décider du sort de `backtest_engine` legacy (🟠2, 🟡5)
**Option A (recommandé)** : archiver `code/backtest_engine.pyx` (et `.cpp`) dans un dossier `code/legacy/`, retirer l'extension de `config/setup.py`. Le module ne contribue plus à la production depuis l'adoption de `backtest_engine_standard`.  
**Option B** : ajouter un test dédié minimal pour `TestLegacyEngine` + documenter son rôle.

### Priorité 3 — Documenter le build step dans README (🟡4)
Ajouter une section "Build Cython" dans `README.md` :

```powershell
# Requis après git clone ou modification .pyx
.venv\Scripts\python.exe config/setup.py build_ext --inplace
Copy-Item code\*.pyd code\bin\  # ou ajuster le outdir dans setup.py
```

### Priorité 4 — Compléter couverture tests (🟡6)
Ajouter dans `test_p1_p2_fixes.py` des cas couvrant `partial_enabled=True`, `breakeven_enabled=True`, et `cooldown_candles > 0` via `backtest_engine_standard` directement.

### Priorité 5 — Harmoniser style `.pyi` (🟡7)
Aligner `backtest_engine.pyi` sur le style de `backtest_engine_standard.pyi` (`Dict[str, Any]` from `typing`) pour cohérence avec le `language_level='3'` ciblant Python 3.11.

---

*Fin d'audit — MULTI_ASSETS Cython v2026-03-20*
