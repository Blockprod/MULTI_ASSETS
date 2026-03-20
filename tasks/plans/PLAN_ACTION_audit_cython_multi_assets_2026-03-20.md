# PLAN D'ACTION — MULTI_ASSETS — 2026-03-20
Sources : `tasks/audits/audit_cython_multi_assets.md`
Total : 🔴 0 · 🟠 3 · 🟡 4 · Effort estimé : 1.5 jours · **Statut : ✅ TERMINÉ 2026-03-20**

---

## PHASE 1 — CRITIQUES 🔴

*Aucune correction critique.*

---

## PHASE 2 — MAJEURES 🟠

### [C-01] Supprimer le stub orphelin `calculate_indicators_fast`
Fichier : `code/bin/backtest_engine_standard.pyi`
Problème : `calculate_indicators_fast()` est déclaré dans le stub mais absent de
  `code/backtest_engine_standard.pyx`. Tout outil de type-checking (Pyright, mypy)
  ou code qui l'importe croira pouvoir appeler cette fonction — l'appel échouera
  avec `AttributeError` à l'exécution.
Correction : Supprimer les lignes déclarant `calculate_indicators_fast` dans le `.pyi`.
  Vérifier qu'aucun appelant en dehors du stub ne référence cette fonction.
Validation :
  ```powershell
  # Vérifier qu'aucun appelant dans le code source
  grep -r "calculate_indicators_fast" code/src/
  # Attendu : aucun résultat

  .venv\Scripts\python.exe -m pyright --project pyrightconfig.json
  # Attendu : zéro erreur de référence for calculate_indicators_fast

  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 685 passed
  ```
Dépend de : Aucune
Effort : 0.25j
Statut : ⏳

---

### [C-02] Archiver `backtest_engine` (legacy) — retrait du build actif
Fichier : `code/backtest_engine.pyx` · `code/backtest_engine.cpp` ·
          `code/bin/backtest_engine*.pyd` · `code/bin/backtest_engine.pyi` ·
          `config/setup.py`
Problème : `backtest_engine` (legacy) n'est jamais importé en production
  (`backtest_runner.py` importe exclusivement `backtest_engine_standard`).
  Il est pourtant compilé pour cp311 et cp313, maintenu dans `config/setup.py`,
  et expose une signature incompatible avec `backtest_engine_standard` (paramètre
  `open` en position 2 vs 11, DEF constants vs runtime params).
  Risque de dérive silencieuse à chaque évolution du moteur actif.
Correction :
  Déplacer `code/backtest_engine.pyx` et `code/backtest_engine.cpp` vers
  `code/legacy/` (archivage, pas suppression). Retirer l'extension
  `backtest_engine` de la liste dans `config/setup.py`. Supprimer ou déplacer
  `code/bin/backtest_engine*.pyd` et `code/bin/backtest_engine.pyi` vers
  `code/bin/legacy/`. Vérifier qu'aucun test ni module ne l'importe.
Validation :
  ```powershell
  # Vérifier aucun import actif du legacy
  grep -r "import backtest_engine" code/src/ tests/
  # Attendu : seuls les imports de backtest_engine_standard

  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 685 passed (aucun test ne dépend du legacy)
  ```
Dépend de : C-01 (audit complet avant archivage)
Effort : 0.5j
Statut : ⏳

---

### [C-03] Documenter la divergence architecturale DEF constants (legacy)
Fichier : `code/backtest_engine.pyx:34-39` (ou `code/legacy/backtest_engine.pyx`
  après C-02)
Problème : `backtest_engine.pyx` utilise `DEF ATR_MULTIPLIER=5.5`,
  `DEF STOP_LOSS_ATR_MULT=2.0` — constantes figées à la compilation, non
  modifiables en runtime. Divergence architecturale majeure avec
  `backtest_engine_standard` qui paramétrise tout. Si le legacy est un jour
  réactivé par erreur, ces valeurs hardcodées produiront des comportements
  inattendus sans erreur explicite.
Correction :
  **Si C-02 est appliqué (archivage)** : ajouter en tête du fichier archivé un
  commentaire `# ARCHIVED 2026-03-20 — DEF constants non configurables runtime,
  remplacé par backtest_engine_standard`. Pas d'autre modification.
  **Si C-02 est rejeté** : ajouter une entrée dans `knowledge/binance_constraints.md`
  documentant la divergence et l'interdiction de réactiver le legacy sans migration.
Validation :
  ```powershell
  # Aucun test supplémentaire requis — correction documentaire uniquement
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : 685 passed
  ```
Dépend de : C-02
Effort : 0.1j
Statut : ⏳

---

## PHASE 3 — MINEURES 🟡

### [C-04] Documenter le build Cython dans README
Fichier : `README.md`
Problème : Les `.pyd` et `.so` sont dans `.gitignore` (non commités). Un
  développeur clonant le repo ne peut pas lancer les backtests Cython sans
  recompiler — aucune instruction dans `README.md`.
Correction : Ajouter une section "Build Cython" dans `README.md` expliquant
  la commande de recompilation et le déplacement des artefacts vers `code/bin/`.
  ```powershell
  # Exemple de section à ajouter :
  ## Build Cython
  # Requis après git clone ou modification .pyx
  .venv\Scripts\python.exe config/setup.py build_ext --inplace
  # Les .pyd générés dans code/ doivent être copiés dans code/bin/
  ```
Validation :
  ```powershell
  # Vérification documentaire — pas de test automatisé
  # Relire README.md et vérifier la présence de la section Build Cython
  ```
Dépend de : Aucune
Effort : 0.25j
Statut : ⏳

---

### [C-05] Tests pour paramètres avancés de `backtest_engine_standard`
Fichier : `tests/test_p1_p2_fixes.py`
Problème : `backtest_engine_standard` expose `partial_enabled`, `breakeven_enabled`,
  `cooldown_candles`, `mtf_bullish`, `use_mtf_filter` — ces paramètres ne sont pas
  couverts par les tests Cython actuels. Un bug dans leur traitement dans le code
  Cython compilé passerait inaperçu.
Correction : Ajouter dans `TestCythonEngineP1P2` des méthodes de test :
  - `test_partial_take_profit_triggers` : vérifie qu'avec `partial_enabled=True`
    et des prix atteignant `partial_threshold_1`, la position est partiellement clôturée.
  - `test_breakeven_stop_moves` : vérifie que le stop remonte au prix d'entrée
    quand `breakeven_enabled=True` et le trigger est atteint.
  - `test_cooldown_blocks_reentry` : vérifie qu'après une sortie, `cooldown_candles`
    empêche un rachat immédiat.
  - `test_mtf_filter_blocks_buy` : vérifie que `mtf_bullish=False` bloque les
    achats même avec signal valide.
Validation :
  ```powershell
  .venv\Scripts\python.exe -m pytest tests/test_p1_p2_fixes.py -v -q
  # Attendu : tous les tests passent, y compris les 4 nouveaux

  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : ≥ 689 passed (685 + 4 nouveaux)
  ```
Dépend de : Aucune (indépendant de C-02)
Effort : 0.5j
Statut : ⏳

---

### [C-06] Harmoniser le style des stubs `.pyi` inter-modules
Fichier : `code/bin/backtest_engine.pyi`
Problème : `backtest_engine.pyi` utilise `dict[str, object]` (style Python 3.10+
  sans import) là où `backtest_engine_standard.pyi` utilise `Dict[str, Any]`
  (typing module). Incohérence cosmétique sans impact fonctionnel, mais
  déroutant lors d'un audit ou d'une extension ultérieure.
Correction :
  **Si C-02 est appliqué (archivage)** : sans objet (fichier archivé).
  **Sinon** : aligner `backtest_engine.pyi` sur le style de `backtest_engine_standard.pyi`
  — ajouter `from typing import Any` en tête et remplacer `dict[str, object]`
  par `Dict[str, Any]`.
Validation :
  ```powershell
  .venv\Scripts\python.exe -m pyright --project pyrightconfig.json
  # Attendu : zéro nouvelle erreur de type
  ```
Dépend de : C-02 (sans objet si C-02 archive le legacy)
Effort : 0.1j
Statut : ⏳

---

### [C-07] Mettre à jour la structure `tasks/WORKFLOW.md`
Fichier : `tasks/WORKFLOW.md`
Problème : Le bloc `STRUCTURE COMPLÈTE DU DOSSIER TASKS` en bas du fichier ne
  liste pas `audit_cython_multi_assets.md` dans `tasks/audits/` ni
  `PLAN_ACTION_cython_[DATE].md` dans `tasks/plans/`.
Correction : Ajouter les deux lignes manquantes dans les listes correspondantes.
Validation :
  ```powershell
  # Vérification manuelle — aucun test automatisé
  # Relire tasks/WORKFLOW.md, section STRUCTURE COMPLÈTE
  ```
Dépend de : Aucune
Effort : 0.05j
Statut : ⏳

---

## SÉQUENCE D'EXÉCUTION

```
C-01 (stub orphelin)
  └─▶ C-02 (archiver legacy backtest_engine)
        └─▶ C-03 (documenter divergence DEF constants)
              └─▶ C-06 (style .pyi — sans objet si C-02 fait)

C-04 (README build) — parallèle, indépendant
C-05 (tests params avancés) — parallèle, indépendant
C-07 (WORKFLOW.md structure) — parallèle, indépendant
```

Ordre recommandé (en tenant compte des dépendances et du risque) :
 1. C-01 — correction rapide, zéro risque
 2. C-07 — documentation pure, 5 minutes
 3. C-04 — documentation README, 15 minutes
 4. C-02 — décision + archivage (bloquer une demi-journée, rouler les tests)
 5. C-03 — résolu en même temps que C-02 (commentaire en tête du fichier archivé)
 6. C-05 — écriture tests, à faire sur une session dédiée
 7. C-06 — résolu par C-02 ou correction rapide si C-02 rejeté

---

## CRITÈRES PASSAGE EN PRODUCTION

- [ ] Zéro 🔴 ouvert
- [ ] `pytest tests/ : 100% pass`
- [ ] Zéro credential dans les logs
- [ ] Stop-loss garanti après chaque BUY
- [ ] Paper trading validé 5 jours minimum

---

## TABLEAU DE SUIVI

| ID | Titre | Sévérité | Fichier | Effort | Statut | Date |
|---|---|---|---|---|---|---|
| C-01 | Stub orphelin `calculate_indicators_fast` | 🟠 | `code/bin/backtest_engine_standard.pyi` | 0.25j | ✅ | 2026-03-20 |
| C-02 | Archiver `backtest_engine` legacy | 🟠 | `code/backtest_engine.pyx` · `config/setup.py` | 0.5j | ✅ | 2026-03-20 |
| C-03 | Documenter divergence DEF constants | 🟠 | `code/legacy/backtest_engine.pyx` | 0.1j | ✅ | 2026-03-20 |
| C-04 | README : section Build Cython | 🟡 | `README.md` | 0.25j | ✅ | 2026-03-20 |
| C-05 | Tests params avancés `backtest_engine_standard` | 🟡 | `tests/test_p1_p2_fixes.py` | 0.5j | ✅ | 2026-03-20 |
| C-06 | Harmoniser style `.pyi` | 🟡 | `code/bin/backtest_engine.pyi` | 0.1j | ✅ | 2026-03-20 (sans objet — C-02 archivé) |
| C-07 | WORKFLOW.md : structure audits/plans | 🟡 | `tasks/WORKFLOW.md` | 0.05j | ✅ | 2026-03-20 |
