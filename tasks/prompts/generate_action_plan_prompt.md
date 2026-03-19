---
modele: sonnet-4.6
mode: agent
contexte: codebase
produit: tasks/plans/PLAN_ACTION_[NOM_AUDIT]_[DATE].md
derniere_revision: 2026-03-18
---

#codebase

Je suis le chef de projet MULTI_ASSETS.

Scanne le workspace, détecte tous les fichiers d'audit
disponibles (*.md contenant : critique, P0, 🔴,
NON CONFORME, fichier:ligne) et affiche-les numérotés.

Demande : "Quel(s) audit(s) utiliser ?
[TOUS] ou [1][2]..."

Puis génère dans `tasks/plans/` le fichier plan en nommant
le fichier d'après l'audit source (sans extension) :
  PLAN_ACTION_[NOM_AUDIT]_[DATE].md

Exemple : audit source = `audit_structural.md`
  → `tasks/plans/PLAN_ACTION_audit_structural_2026-03-18.md`

Exemple : audit source = `AUDIT_TECHNIQUE_MULTI_ASSETS.md`
  → `tasks/plans/PLAN_ACTION_AUDIT_TECHNIQUE_MULTI_ASSETS_2026-03-18.md`

─────────────────────────────────────────────
STRUCTURE OBLIGATOIRE DU FICHIER
─────────────────────────────────────────────
# PLAN D'ACTION — MULTI_ASSETS — [DATE]
Sources : [audits utilisés]
Total : 🔴 X · 🟠 X · 🟡 X · Effort estimé : X jours

## PHASE 1 — CRITIQUES 🔴
## PHASE 2 — MAJEURES 🟠
## PHASE 3 — MINEURES 🟡

Pour chaque correction :
### [C-XX] Titre
Fichier : src/chemin/fichier.py:ligne
Problème : [description]
Correction : [ce qui doit être fait]
Validation :
  .venv\Scripts\python.exe -m pytest tests/ -x -q
  # Attendu : [résultat attendu]
Dépend de : [C-XX ou Aucune]
Statut : ⏳

## SÉQUENCE D'EXÉCUTION
[ordre tenant compte des dépendances]

## CRITÈRES PASSAGE EN PRODUCTION
- [ ] Zéro 🔴 ouvert
- [ ] pytest tests/ : 100% pass
- [ ] Zéro credential dans les logs
- [ ] Stop-loss garanti après chaque BUY
- [ ] Paper trading validé 5 jours minimum

## TABLEAU DE SUIVI
| ID | Titre | Sévérité | Fichier | Effort | Statut | Date |

─────────────────────────────────────────────
RÈGLES
─────────────────────────────────────────────
- Ne modifier aucun fichier de code source
- Ne jamais modifier .env ou states/bot_state.json
- Problème dans plusieurs audits = une seule entrée
- Effort inconnu → "À ESTIMER"
- Fichier compatible avec execute_corrections_prompt.md
- Nommer le fichier plan d'après l'audit source (voir en-tête)

Confirme dans le chat uniquement :
"✅ tasks/plans/PLAN_ACTION_[NOM_AUDIT]_[DATE].md créé
 🔴 X · 🟠 X · 🟡 X · Effort : X jours
 👉 Lance execute_corrections_prompt.md pour démarrer."