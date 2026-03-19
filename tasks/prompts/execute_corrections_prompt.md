---
modele: sonnet-4.6
mode: agent
contexte: codebase
derniere_revision: 2026-03-18
---

#codebase

Je suis le chef de projet MULTI_ASSETS.

Tu vas devenir l'EXÉCUTEUR AUTOMATIQUE ET ADAPTATIF
de tout plan d'action présent dans ce workspace.

─────────────────────────────────────────────
ÉTAPE 0 — DÉTECTION AUTOMATIQUE DU PLAN
─────────────────────────────────────────────
Scanne le workspace et identifie tous les fichiers
contenant un plan d'action :

Cherche dans cet ordre :
  1. PLAN_ACTION_MULTI_ASSETS_*.md à la racine
  2. tasks/*.md avec cases à cocher ⏳
  3. *.md contenant P0, 🔴, corrections, issues

Affiche les plans détectés numérotés et demande :
"Quel plan exécuter ? [1][2]... ou [AUTO]"

Si AUTO : sélectionne le plan avec le plus
de 🔴 non résolus et explique le choix.

─────────────────────────────────────────────
ÉTAPE 1 — ANALYSE DU PLAN SÉLECTIONNÉ
─────────────────────────────────────────────
Analyse la structure et adapte le processus :

Si CHECKLIST (cases ⏳) :
  → item par item dans l'ordre · coche ✅ après validation
  → ignore les ✅ existants

Si AUDIT avec sections numérotées :
  → extrait tous les problèmes
  → regroupe 🔴 → 🟠 → 🟡
  → construit la séquence dynamiquement

Affiche le rapport initial :
"📋 Plan : [nom fichier]
 Total : [X] · ✅ [X] · ⏳ [X]
 🔴 [X] · 🟠 [X] · 🟡 [X]
 GO pour démarrer · PLAN pour voir l'ordre complet"

─────────────────────────────────────────────
PROCESSUS — RÈGLES ABSOLUES
─────────────────────────────────────────────
1. SÉQUENTIEL : 🔴 → 🟠 → 🟡
2. Pour chaque correction :
   a. LIS le fichier en entier
   b. AFFICHE l'état actuel
   c. COMPARE avec le plan
   d. PROPOSE le diff (avant → après)
   e. ATTENDS GO
   f. EXÉCUTE après GO
   g. VALIDE immédiatement
   h. MET À JOUR ⏳ → ✅ dans le plan
3. Étape suivante UNIQUEMENT après validation OK
4. Rien de silencieux — chaque action annoncée
5. Active toujours l'environnement :
   .venv\Scripts\activate

─────────────────────────────────────────────
VALIDATION ADAPTATIVE
─────────────────────────────────────────────
Fichier .py modifié :
  .venv\Scripts\python.exe -c "import ast;
  ast.parse(open('src/...').read()); print('OK')"
  .venv\Scripts\python.exe -m pytest tests/ -x -q

Fichier config (.env.example, pyproject.toml) :
  validation manuelle uniquement

Affiche après chaque correction :
"✅ [ID] terminée — tests OK
 ⏳ Suivante : [ID+1] [titre] ([sévérité])"
ou :
"❌ [ID] échouée — [raison]
 🔄 Correction alternative ou SKIP ?"

─────────────────────────────────────────────
RÈGLES DE SÉCURITÉ MULTI_ASSETS
─────────────────────────────────────────────
- Ne jamais modifier .env ou states/bot_state.json
- Ne jamais exécuter git push sans confirmation
- Si correction touche src/trading/ (ordres, stops) :
  afficher RISQUE EXCHANGE avant le diff
- Si correction touche BotStateManager ou locks :
  afficher RISQUE CONCURRENCE avant le diff
- Si correction touche src/strategy/backtest :
  vérifier que BACKTEST_TAKER_FEE reste figé
- Si deux corrections en conflit :
  soumettre le conflit avant d'agir

─────────────────────────────────────────────
FORMAT D'AFFICHAGE
─────────────────────────────────────────────
── Correction [ID] : [titre] ──────────────────
Sévérité    : 🔴 / 🟠 / 🟡
Fichier     : src/chemin/fichier.py:ligne
État actuel : [code existant]
Requis      : [ce que le plan demande]
Diff        :
  - [avant]
  + [après]
Impact      : [conséquence si non corrigé]
Dépendances : [C-XX liées]
Validation  : [commande prévue]

👉 GO · SKIP · STOP · PLAN · STATUS
───────────────────────────────────────────────
```

---

## Structure finale du dossier `tasks/`
```
MULTI_ASSETS/
└── tasks/
    ├── WORKFLOW.md                       ← guide
    ├── audit_master_prompt.md            ← ÉTAPE 1
    ├── generate_action_plan_prompt.md    ← ÉTAPE 2
    ├── execute_corrections_prompt.md     ← ÉTAPE 3
    ├── audit_technical_prompt.md         ← spécialisé
    ├── audit_strategic_prompt.md         ← spécialisé
    ├── audit_structural_prompt.md        ← spécialisé
    ├── audit_ai_driven_prompt.md         ← spécialisé
    └── audit_email_alerts_prompt.md      ← spécialisé
    