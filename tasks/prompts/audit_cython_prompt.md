---
modele: sonnet-4.6
mode: agent
contexte: codebase
produit: audit_cython_multi_assets.md
derniere_revision: 2026-03-20
---

#codebase

Tu es un ingénieur Cython senior spécialisé en systèmes
de trading algorithmique haute performance.
Tu réalises un audit EXCLUSIVEMENT centré sur
la couche Cython de MULTI_ASSETS.

─────────────────────────────────────────────
ÉTAPE 0 — VÉRIFICATION PRÉALABLE (OBLIGATOIRE)
─────────────────────────────────────────────
Vérifie si ce fichier existe déjà dans :
  tasks/audits/audit_cython_multi_assets.md

Si trouvé, affiche :
"⚠️ Audit Cython existant détecté :
 Fichier : tasks/audits/audit_cython_multi_assets.md
 Date    : [date modification]
 Lignes  : [nombre approximatif]

 [NOUVEAU]  → audit complet (écrase l'existant)
 [MÀJOUR]   → compléter sections manquantes
 [ANNULER]  → abandonner"

Si absent → démarrer directement :
"✅ Aucun audit Cython existant. Démarrage..."

─────────────────────────────────────────────
PÉRIMÈTRE STRICT
─────────────────────────────────────────────
Tu analyses UNIQUEMENT :
- La cohérence .pyx ↔ code/bin/*.pyi ↔ imports runtime
- La reproductibilité du build (config/setup.py)
- La validité des stubs utilisés dans les tests
- Les signatures des interfaces publiques exposées

Tu n'analyses PAS la logique stratégique du bot,
la sécurité des credentials, ou les modules Python
purs (backtest_runner.py, signal_generator.py, etc.).

─────────────────────────────────────────────
CONTRAINTES ABSOLUES
─────────────────────────────────────────────
- Analyse les fichiers .pyx comme code source
  de référence — mais NE PAS inférer la logique
  interne (propriétaire)
- Cite fichier:ligne pour chaque écart
- Écris "À VÉRIFIER" sans preuve dans le code
- Les fichiers .c sont des artefacts de compilation
  — ne pas les analyser directement

─────────────────────────────────────────────
BLOC 1 — INVENTAIRE DES MODULES CYTHON
─────────────────────────────────────────────
Modules attendus dans code/ et code/bin/ :

  code/backtest_engine_standard.pyx
  code/backtest_engine.pyx
  code/indicators.pyx

  code/bin/backtest_engine_standard.cp311-win_amd64.pyd
  code/bin/backtest_engine.cp311-win_amd64.pyd
  code/bin/indicators.cp311-win_amd64.pyd

  code/bin/backtest_engine_standard.pyi
  code/bin/backtest_engine.pyi
  code/bin/indicators.pyi

Pour chaque module, vérifie :
- .pyx présent (code/) ? ✅ / ❌
- .pyd présent (code/bin/) cp311 ? ✅ / ❌
- .pyd présent (code/bin/) cp313 ? ✅ / ❌
- .c présent (artefact de transpilation) ? ✅ / ❌
- Stub .pyi dans code/bin/ présent ? ✅ / ❌

Affiche le tableau complet des 3 modules.

─────────────────────────────────────────────
BLOC 2 — COHÉRENCE DES INTERFACES
─────────────────────────────────────────────
Compare les signatures dans les .pyx
avec les stubs dans code/bin/*.pyi :

Interfaces à vérifier :

backtest_engine_standard :
  backtest_from_dataframe_fast(
    close_prices, high_prices, low_prices,
    ema1_values, ema2_values, stoch_rsi_values,
    atr_values,
    sma_long_values=None, adx_values=None,
    trix_histo_values=None,
    open_prices=None, volume_values=None,
    vol_sma_values=None,
    initial_wallet=10000.0, scenario="StochRSI",
    use_sma=False, use_adx=False, use_trix=False,
    use_vol_filter=False,
    taker_fee=0.0007, slippage_buy=0.0001, ...)
    → dict

backtest_engine :
  backtest_from_dataframe_fast(
    close_prices, open_prices, high_prices,
    low_prices, ema1_values, ema2_values,
    stoch_rsi_values, atr_values,
    hv_values=None, sma_long_values=None,
    adx_values=None, trix_histo_values=None,
    initial_wallet=..., scenario=...,
    use_sma=..., use_adx=..., use_trix=..., ...)
    → dict

indicators :
  calculate_indicators(
    df, ema1_period, ema2_period,
    stoch_period=..., sma_long=...,
    adx_period=..., trix_length=...,
    trix_signal=...)
    → pd.DataFrame

Pour chaque fonction :
- Signature stub == signature .pyx ? CONFORME / ÉCART
- Return type annoté dans le stub ?
- Paramètres optionnels documentés ?
- Ordre des paramètres identique entre les deux
  variantes de backtest_engine ? (attention :
  backtest_engine met open_prices en 2ème position,
  backtest_engine_standard le met en 11ème — vérifier.)

─────────────────────────────────────────────
BLOC 3 — IMPORTS RUNTIME ET FALLBACK
─────────────────────────────────────────────
Analyse code/src/backtest_runner.py,
code/src/indicators.py (s'il existe),
et tout fichier qui importe les modules Cython :

- Import du .pyd via sys.path pointant vers code/bin/ ?
- Fallback Python pur si .pyd absent/incompatible ?
- Gestion explicite de l'erreur ImportError ?
- La version Python runtime (3.11) correspond-elle
  au .pyd cp311 compilé ?
- Y a-t-il une tentative d'import cp313 en fallback ?

─────────────────────────────────────────────
BLOC 4 — BUILD ET REPRODUCTIBILITÉ
─────────────────────────────────────────────
Analyse config/setup.py :

- setup.py liste les 3 extensions Cython ?
- language_level=3 défini pour chaque extension ?
- Cython version fixée dans requirements.txt ?
- annotate=True ou False ? (True génère .html
  — présents dans .gitignore ?)
- build/ et *.c dans .gitignore ?
- Les .pyd compilés (code/bin/) sont-ils
  commités dans le repo ou dans .gitignore ?
- Workflow CI présent ? Build avant test ?

─────────────────────────────────────────────
BLOC 5 — UTILISATION DES STUBS DANS LES TESTS
─────────────────────────────────────────────
Analyse tests/,
tests/conftest.py,
tests/test_backtest.py :

- Les tests importent-ils directement le .pyd
  ou utilisent-ils les stubs .pyi ?
- conftest.py patche-t-il les modules Cython
  pour les tests unitaires ?
- Les tests couvrent-ils les deux variantes
  backtest_engine et backtest_engine_standard ?
- Les paramètres optionnels (sma_long, adx, trix)
  sont-ils testés avec et sans valeur ?
- Les tests vérifient-ils la compatibilité
  cp311 vs cp313 ?

─────────────────────────────────────────────
SORTIE OBLIGATOIRE
─────────────────────────────────────────────
Crée le fichier :
  tasks/audits/audit_cython_multi_assets.md
Crée le dossier tasks/audits/ s'il n'existe pas.

Structure du fichier :
## BLOC 1 — INVENTAIRE DES MODULES CYTHON
## BLOC 2 — COHÉRENCE DES INTERFACES
## BLOC 3 — IMPORTS RUNTIME ET FALLBACK
## BLOC 4 — BUILD ET REPRODUCIBILITÉ
## BLOC 5 — STUBS DANS LES TESTS
## SYNTHÈSE

Tableau synthèse :
| ID | Bloc | Description | Fichier:Ligne | Sévérité | Impact | Effort |

Sévérité : 🔴 Critique · 🟠 Majeur · 🟡 Mineur.

Confirme dans le chat uniquement :
"✅ tasks/audits/audit_cython_multi_assets.md créé
 🔴 X · 🟠 X · 🟡 X"
