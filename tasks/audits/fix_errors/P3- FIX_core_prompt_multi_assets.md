---
modele: sonnet-4.6
mode: agent
contexte: codebase
produit: tasks/audits/fix_errors/fix_results/BATCH_result.md
derniere_revision: 2026-04-06
creation: 2026-04-06 à 21:10
---

#codebase

Tu es un Senior Python Engineer spécialisé typage statique pandas / pyright / systèmes de trading.
Tu corriges UN seul batch du plan MULTI_ASSETS.

─────────────────────────────────────────────
INPUT
─────────────────────────────────────────────
Lire `tasks/audits/fix_errors/fix_results/PLAN_result.md`.
Traiter le batch demandé (précisé par l'utilisateur ou le batch 1 par défaut).
Lire aussi `tasks/lessons.md` avant de commencer — éviter de répéter
les erreurs déjà documentées.

─────────────────────────────────────────────
PROTOCOLE DE CORRECTION — 5 ÉTAPES
─────────────────────────────────────────────

### ÉTAPE A — LIRE avant d'écrire
Pour chaque fichier du batch :
1. Lire les lignes d'erreur exactes (pyright output de P1)
2. Lire le fichier autour de chaque ligne (+/- 15 lignes)
3. Identifier la cause racine (pas le symptôme)

### ÉTAPE B — APPLIQUER les patterns MULTI_ASSETS

**CATALOGUE DE FIXES OBLIGATOIRES :**

```python
# ── Typing : DataFrame subscript ──────────────────────────
# ❌  y = df[sym]
# ✅  y = pd.Series(df[sym])

# ── Typing : sous-DataFrame ───────────────────────────────
# ❌  sub = df[cols]
# ✅  sub = pd.DataFrame(df[cols])

# ── Typing : rolling iloc ─────────────────────────────────
# ❌  v = df.rolling(n).mean().iloc[-1]
# ✅  v = float(pd.Series(df.rolling(n).mean()).iloc[-1])

# ── Typing : Timestamp NaTType ────────────────────────────
# ❌  ts = pd.Timestamp(x)
# ✅  ts = cast(pd.Timestamp, pd.Timestamp(str(x)))
#    (nécessite : from typing import cast)

# ── Typing : Optional manquant ────────────────────────────
# ❌  def foo(x: str) -> str:
# ✅  def foo(x: Optional[str]) -> Optional[str]:
#    (nécessite : from typing import Optional)

# ── Typing : Dict annotation manquante ────────────────────
# ❌  def foo() -> dict:
# ✅  def foo() -> Dict[str, Any]:
#    (nécessite : from typing import Dict, Any)

# ── datetime.utcnow() déprécié ────────────────────────────
# ❌  now = datetime.utcnow()
# ✅  now = datetime.now(timezone.utc)
#    (nécessite : from datetime import datetime, timezone)

# ── Silent except — interdit absolu ───────────────────────
# ❌  except Exception:
#         pass
# ❌  except Exception:
#         ...
# ✅  except Exception as _exc:
#         logger.debug("[MODULE] Erreur ignorée: %s", _exc)

# ── ARG002/ARG004 : paramètre inutilisé ───────────────────
# ❌  def method(self, unused_param):
# ✅  Préfixer avec _ pour signifier volontairement ignoré :
#     def method(self, _unused_param):
#   OU connecter réellement le paramètre au calcul si possible.

# ── Thread safety — bot_state ─────────────────────────────
# ❌  bot_state[pair] = new_value          # écriture non protégée
# ✅  with _bot_state_lock:
#         bot_state[pair] = new_value
```

**IMPORT À AJOUTER si absent :**
```python
from typing import cast, Optional, Dict, Any, List, Union, Tuple
from datetime import datetime, timezone
```

### ÉTAPE C — CONTRAINTES ABSOLUES MULTI_ASSETS

```
❌ INTERDIT — jamais écrire ces lignes :
   # type: ignore                     → toujours corriger avec le bon type
   except Exception: pass             → toujours logger.debug/warning/error
   except Exception: ...              → idem
   print()                            → utiliser logger.info/debug/warning
   datetime.utcnow()                  → utiliser datetime.now(timezone.utc)
   TRAILING_STOP_MARKET sur Spot      → NotImplementedError (ordre Futures seulement)
   start_date = "2023-01-01"          → utiliser _fresh_start_date()
   backtest_taker_fee = <valeur>      → NE JAMAIS modifier au runtime
   logger.info(config.api_key)        → JAMAIS logguer les clés en clair
   bot_state[x] = y (hors lock)       → toujours utiliser _bot_state_lock
```

**RÈGLE CYTHON (NE PAS MODIFIER LES .pyx) :**
Les fichiers `code/bin/backtest_engine_standard.pyd` et `code/bin/indicators.pyd`
sont des Cython PRÉ-COMPILÉS. Si une erreur vient d'un appel à ces modules :
- Vérifier la signature dans `code/bin/backtest_engine_standard.pyi` ou `code/bin/indicators.pyi`
- Corriger le code Python appelant pour matcher la signature `.pyi`
- NE PAS recompiler — les .pyx sources sont dans `code/` mais la compilation
  nécessite un environnement dédié (config/setup.py)

**RÈGLE MULTI_SYMBOLS.py :**
Ce fichier fait ~3400 lignes et est l'orchestrateur central.
- Toujours lire le fichier `.context.md` correspondant avant toute modif :
  `code/src/MULTI_SYMBOLS.context.md`
- Toutes les modifications bot_state → dans `with _bot_state_lock:`
- Toutes les modifications _runtime → accès via `_runtime.<champ>` (singleton)
- Utiliser des subagents pour l'exploration si nécessaire

### ÉTAPE D — VÉRIFICATION PAR FICHIER (max 3 itérations)

Après chaque fichier corrigé :
```powershell
# 1. Validation syntaxe Python (rapide, avant tout)
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/fichier.py', encoding='utf-8').read()); print('OK')"

# 2. Pyright sur le seul fichier modifié
.venv\Scripts\python.exe -m pyright code/src/fichier.py 2>&1 | Select-Object -Last 5

# 3. Ruff + ARG sur le seul fichier
.venv\Scripts\python.exe -m ruff check code/src/fichier.py --select ARG 2>&1 | Select-Object -Last 5
```

Si encore des erreurs après itération 3 → marquer comme BLOCKER
et passer au fichier suivant sans s'acharner.

### ÉTAPE E — VÉRIFICATION BATCH COMPLÈTE

Quand tous les fichiers du batch sont traités :
```powershell
# Tests ciblés sur les modules concernés (rapide)
.venv\Scripts\python.exe -m pytest tests/ -x -q --tb=short 2>&1 | Select-Object -Last 10

# Si le batch touche un module avec tests dédiés :
# .venv\Scripts\python.exe -m pytest tests/test_<module>.py -v --tb=short
```

─────────────────────────────────────────────
STOP RULE
─────────────────────────────────────────────
- Max 3 itérations par fichier
- Max 20 fichiers par batch
- Si le fix d'un fichier crée de nouvelles erreurs dans un autre :
  noter comme BLOCKER, ne pas cascader indéfiniment
- Si une erreur nécessite de modifier MULTI_SYMBOLS.py + un autre fichier
  en même temps → traiter MULTI_SYMBOLS.py en dernier dans le batch

─────────────────────────────────────────────
SORTIE OBLIGATOIRE
─────────────────────────────────────────────
Mettre à jour `C:\Users\averr\MULTI_ASSETS\tasks\audits\fix_errors\fix_results\BATCH_result.md` avec :

```
BATCH_RESULT:
  batch          : N
  fixed_files    : X
  remaining_errors: Y
  blockers       : ["code/src/fichier.py:L42 — raison"]
  tests          : X passed / Y failed
```

Confirmer dans le chat :
"✅ Batch N terminé · X fixes · Y erreurs restantes · Z tests pass"

SORTIE OBLIGATOIRE :
Tous les résultats doivent être enregistrés dans :
C:\Users\averr\MULTI_ASSETS\tasks\audits\fix_errors\fix_results
