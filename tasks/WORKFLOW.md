---
type: guide
projet: MULTI_ASSETS
exchange: Binance Spot (USDC)
stack: Python 3.11.9 · PM2 · Windows
derniere_revision: 2026-03-18
---

# WORKFLOW — Audit → Plan → Corrections
# MULTI_ASSETS — Bot de trading Binance Spot

Guide d'utilisation des prompts du dossier tasks/.
Suivre les étapes dans l'ordre strict.

---

## ÉTAPE 1 — Audit complet

**Prompt** : `tasks/prompts/audit_master_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Produit** : `tasks/audits/AUDIT_TECHNIQUE_MULTI_ASSETS.md`
```
#file:tasks/prompts/audit_master_prompt.md
Lance cet audit sur le workspace.
```

---

## ÉTAPE 2 — Génération du plan d'action

**Prompt** : `tasks/prompts/generate_action_plan_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/audits/AUDIT_TECHNIQUE_MULTI_ASSETS.md`
**Produit** : `tasks/plans/PLAN_ACTION_AUDIT_TECHNIQUE_MULTI_ASSETS_[DATE].md`
```
#file:tasks/prompts/generate_action_plan_prompt.md
Génère le plan d'action depuis l'audit disponible.
```

---

## ÉTAPE 3 — Exécution des corrections

**Prompt** : `tasks/prompts/execute_corrections_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/plans/PLAN_ACTION_AUDIT_TECHNIQUE_MULTI_ASSETS_[DATE].md`
**Produit** : corrections appliquées · statuts ⏳ → ✅
```
#file:tasks/prompts/execute_corrections_prompt.md
Démarre l'exécution du plan d'action disponible.
```

---

## AUDITS SPÉCIALISÉS (optionnels)

À lancer après l'ÉTAPE 1 pour approfondir
une dimension spécifique.

### Audit Technique & Sécurité

#### Étape A — Audit

**Prompt** : `tasks/prompts/audit_technical_prompt.md`
**Mode**   : Ask
**Modèle** : Sonnet 4.6
**Dimension** : Sécurité credentials Binance ·
               Thread-safety · Robustesse API ·
               Gestion erreurs silencieuses
**Produit** : `tasks/audits/audit_technical.md`
```
#file:tasks/prompts/audit_technical_prompt.md
Lance cet audit sur le workspace.
```

#### Étape B — Génération du plan d'action

**Prompt** : `tasks/prompts/generate_action_plan_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/audits/audit_technical.md`
**Produit** : `tasks/plans/PLAN_ACTION_technical_[DATE].md`
```
#file:tasks/prompts/generate_action_plan_prompt.md
Génère le plan d'action depuis l'audit disponible.
```

#### Étape C — Exécution des corrections

**Prompt** : `tasks/prompts/execute_corrections_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/plans/PLAN_ACTION_technical_[DATE].md`
**Produit** : corrections appliquées · statuts ⏳ → ✅
```
#file:tasks/prompts/execute_corrections_prompt.md
Démarre l'exécution du plan d'action disponible.
```

---

### Audit Stratégique

#### Étape A — Audit

**Prompt** : `tasks/prompts/audit_strategic_prompt.md`
**Mode**   : Ask
**Modèle** : Sonnet 4.6
**Dimension** : Biais backtest · Walk-forward ·
               Cohérence signaux · Frais · Slippage
**Produit** : `tasks/audits/audit_strategic.md`
```
#file:tasks/prompts/audit_strategic_prompt.md
Lance cet audit sur le workspace.
```

#### Étape B — Génération du plan d'action

**Prompt** : `tasks/prompts/generate_action_plan_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/audits/audit_strategic.md`
**Produit** : `tasks/plans/PLAN_ACTION_strategic_[DATE].md`
```
#file:tasks/prompts/generate_action_plan_prompt.md
Génère le plan d'action depuis l'audit disponible.
```

#### Étape C — Exécution des corrections

**Prompt** : `tasks/prompts/execute_corrections_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/plans/PLAN_ACTION_strategic_[DATE].md`
**Produit** : corrections appliquées · statuts ⏳ → ✅
```
#file:tasks/prompts/execute_corrections_prompt.md
Démarre l'exécution du plan d'action disponible.
```

---

### Audit Structurel

#### Étape A — Audit

**Prompt** : `tasks/prompts/audit_structural_prompt.md`
**Mode**   : Ask
**Modèle** : Sonnet 4.6
**Dimension** : Architecture src/ · Couplage modules ·
               SRP · Dette technique
**Produit** : `tasks/audits/audit_structural.md`
```
#file:tasks/prompts/audit_structural_prompt.md
Lance cet audit sur le workspace.
```

#### Étape B — Génération du plan d'action

**Prompt** : `tasks/prompts/generate_action_plan_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/audits/audit_structural.md`
**Produit** : `tasks/plans/PLAN_ACTION_structural_[DATE].md`
```
#file:tasks/prompts/generate_action_plan_prompt.md
Génère le plan d'action depuis l'audit disponible.
```

#### Étape C — Exécution des corrections

**Prompt** : `tasks/prompts/execute_corrections_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/plans/PLAN_ACTION_structural_[DATE].md`
**Produit** : corrections appliquées · statuts ⏳ → ✅
```
#file:tasks/prompts/execute_corrections_prompt.md
Démarre l'exécution du plan d'action disponible.
```

---

### Audit Alertes Email

#### Étape A — Audit

**Prompt** : `tasks/prompts/audit_email_alerts_prompt.md`
**Mode**   : Ask
**Modèle** : Sonnet 4.6
**Dimension** : Couverture notifications trading ·
               Alertes stop-loss · Sécurité emails
**Produit** : `tasks/audits/audit_email_alerts.md`
```
#file:tasks/prompts/audit_email_alerts_prompt.md
Lance cet audit sur le workspace.
```

#### Étape B — Génération du plan d'action

**Prompt** : `tasks/prompts/generate_action_plan_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/audits/audit_email_alerts.md`
**Produit** : `tasks/plans/PLAN_ACTION_email_alerts_[DATE].md`
```
#file:tasks/prompts/generate_action_plan_prompt.md
Génère le plan d'action depuis l'audit disponible.
```

#### Étape C — Exécution des corrections

**Prompt** : `tasks/prompts/execute_corrections_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/plans/PLAN_ACTION_email_alerts_[DATE].md`
**Produit** : corrections appliquées · statuts ⏳ → ✅
```
#file:tasks/prompts/execute_corrections_prompt.md
Démarre l'exécution du plan d'action disponible.
```

---

### Audit IA & ML

#### Étape A — Audit

**Prompt** : `tasks/prompts/audit_ia_ml_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Dimension** : Pertinence ML sur signaux · Régimes de marché ·
               Sizing adaptatif · Agents autonomes ·
               Walk-forward adaptatif · Feature importance
**Produit** : `tasks/audits/audit_IA_ML_multi_assets.md`
```
#file:tasks/prompts/audit_ia_ml_prompt.md
Lance cet audit sur le workspace.
```

#### Étape B — Génération du plan d'action

**Prompt** : `tasks/prompts/generate_action_plan_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/audits/audit_IA_ML_multi_assets.md`
**Produit** : `tasks/plans/PLAN_ACTION_ia_ml_[DATE].md`
```
#file:tasks/prompts/generate_action_plan_prompt.md
Génère le plan d'action depuis l'audit disponible.
```

#### Étape C — Exécution des corrections

**Prompt** : `tasks/prompts/execute_corrections_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/plans/PLAN_ACTION_ia_ml_[DATE].md`
**Produit** : corrections appliquées · statuts ⏳ → ✅
```
#file:tasks/prompts/execute_corrections_prompt.md
Démarre l'exécution du plan d'action disponible.
```

---

### Génération AI-Driven & File Engineering

#### Étape A — Audit

**Prompt** : `tasks/prompts/audit_ai_driven_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Dimension** : Génération fichiers contexte IA ·
               Restructuration AI-native du repo
**Produit** : `tasks/audits/audit_ai_driven.md` · `.claude/` · `.github/` · `agents/` ·
              `knowledge/` · `architecture/`
```
#file:tasks/prompts/audit_ai_driven_prompt.md
Génère les fichiers AI-Driven et le plan
de restructuration File Engineering complet.
```

#### Étape B — Génération du plan d'action

**Prompt** : `tasks/prompts/generate_action_plan_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/audits/audit_ai_driven.md`
**Produit** : `tasks/plans/PLAN_ACTION_ai_driven_[DATE].md`
```
#file:tasks/prompts/generate_action_plan_prompt.md
Génère le plan d'action depuis l'audit disponible.
```

#### Étape C — Exécution des corrections

**Prompt** : `tasks/prompts/execute_corrections_prompt.md`
**Mode**   : Agent
**Modèle** : Sonnet 4.6
**Lit**    : `tasks/plans/PLAN_ACTION_ai_driven_[DATE].md`
**Produit** : corrections appliquées · statuts ⏳ → ✅
```
#file:tasks/prompts/execute_corrections_prompt.md
Démarre l'exécution du plan d'action disponible.
```

---

## MISE À JOUR DES LEÇONS

Après chaque correction appliquée (via `execute_corrections_prompt.md`) ou toute correction manuelle :
si une nouvelle erreur de pattern a été découverte, ajouter une entrée dans `tasks/lessons.md`.

```
#file:tasks/lessons.md
Ajoute une nouvelle entrée L-[N+1] pour le pattern d'erreur suivant :
[description de l'erreur, contexte, règle corrective, ref commit ou fichier]
Sévérité : 🔴 CRITIQUE / 🟡 IMPORTANT / 🔵 INFO
Date : [date du jour]
```

**Règle** : Ne jamais clôturer une session de correction sans avoir vérifié si une leçon mérite d'être ajoutée.

---

## VALIDATIONS RAPIDES

Commandes à lancer à tout moment sans prompt :
```powershell
# Activation environnement
.venv\Scripts\activate

# Syntaxe tous les fichiers Python
.venv\Scripts\python.exe -m py_compile `
  $(Get-ChildItem src/ -Filter "*.py" -Recurse).FullName

# Tests complets
.venv\Scripts\python.exe -m pytest tests/ -q

# Linting
.venv\Scripts\python.exe -m ruff check src/

# Vérification état bot
.venv\Scripts\python.exe check_state.py
```

---

## STRUCTURE COMPLÈTE DU DOSSIER TASKS
```
tasks/
├── WORKFLOW.md                       ← ce fichier
├── lessons.md                        ← leçons capturées
│
├── prompts/                          ← instructions pour agents
│   ├── audit_master_prompt.md        ← ÉTAPE 1
│   ├── generate_action_plan_prompt.md← ÉTAPE 2
│   ├── execute_corrections_prompt.md ← ÉTAPE 3
│   ├── audit_technical_prompt.md     ← audits spécialisés
│   ├── audit_strategic_prompt.md
│   ├── audit_structural_prompt.md
│   ├── audit_email_alerts_prompt.md
│   ├── audit_ia_ml_prompt.md
│   └── audit_ai_driven_prompt.md
│
├── audits/                           ← résultats d'audit
│   ├── AUDIT_TECHNIQUE_MULTI_ASSETS.md
│   ├── audit_technical.md
│   ├── audit_strategic.md
│   ├── audit_structural.md
│   ├── audit_email_alerts.md
│   ├── audit_IA_ML_multi_assets.md
│   └── audit_ai_driven.md
│
└── plans/                            ← plans d'action générés
    ├── PLAN_ACTION_AUDIT_TECHNIQUE_MULTI_ASSETS_[DATE].md
    ├── PLAN_ACTION_audit_technical_[DATE].md
    ├── PLAN_ACTION_audit_strategic_[DATE].md
    ├── PLAN_ACTION_audit_structural_[DATE].md
    ├── PLAN_ACTION_audit_email_alerts_[DATE].md
    ├── PLAN_ACTION_ia_ml_[DATE].md
    └── PLAN_ACTION_audit_ai_driven_[DATE].md
```

---

## RÈGLE D'OR
```
Ne jamais lancer ÉTAPE 3 sans avoir
validé le plan de l'ÉTAPE 2 manuellement.

Ne jamais modifier .env, states/bot_state.json
ou src/watchdog.py sans confirmation explicite.

Ne jamais passer en production sans
paper trading validé minimum 5 jours.
```