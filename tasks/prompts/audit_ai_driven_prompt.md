---
modele: sonnet-4.6
mode: agent
contexte: codebase
derniere_revision: 2026-03-18
---

#codebase

Tu es un Software Architect spécialisé en
AI-Driven Repository Engineering.
Analyse MULTI_ASSETS et génère le plan de
restructuration File Engineering complet.

─────────────────────────────────────────────
CONTRAINTES
─────────────────────────────────────────────
- Génère le contenu RÉEL basé sur le code source
- Pas de templates génériques
- Chaque fichier prêt à copier-coller
- [À COMPLÉTER MANUELLEMENT] si info absente du code
- Ne jamais écraser un fichier existant sans
  afficher son contenu actuel et demander GO

─────────────────────────────────────────────
ÉTAPE 0 — ÉTAT DES LIEUX (OBLIGATOIRE)
─────────────────────────────────────────────
Avant toute action, scanne le workspace et
vérifie l'existence de chaque fichier AI-Driven.

Affiche le rapport d'état complet :

┌─────────────────────────────────────────────────┐
│ ÉTAT DES LIEUX — FICHIERS AI-DRIVEN             │
├──────────────────────────────────────────────────┤
│ .github/copilot-instructions.md  ✅ EXISTE       │
│ .claude/context.md               ❌ ABSENT       │
│ .claude/rules.md                 ⚠️ PARTIEL      │
│ architecture/system_design.md    ❌ ABSENT       │
│ architecture/decisions.md        ❌ ABSENT       │
│ knowledge/binance_constraints.md ❌ ABSENT       │
│ knowledge/trading_constraints.md ❌ ABSENT       │
│ agents/quant_engineer.md         ✅ EXISTE       │
│ agents/risk_manager.md           ❌ ABSENT       │
│ agents/code_auditor.md           ❌ ABSENT       │
└──────────────────────────────────────────────────┘

Légende :
  ✅ EXISTE    → fichier présent et non vide
  ⚠️ PARTIEL  → fichier présent mais incomplet
                 (moins de 20 lignes ou sections
                 manquantes par rapport au standard)
  ❌ ABSENT   → fichier à créer

Pour chaque fichier ✅ EXISTE ou ⚠️ PARTIEL :
  - Affiche le contenu actuel
  - Identifie ce qui est déjà correct
  - Identifie ce qui manque ou est obsolète

Puis affiche le résumé :
"📋 État des lieux terminé.
 ✅ [X] fichiers existants
 ⚠️ [X] fichiers partiels à compléter
 ❌ [X] fichiers absents à créer
 Réponds GO pour démarrer la restructuration
 ou LISTE pour voir l'ordre de création proposé."

─────────────────────────────────────────────
ÉTAPE 1 — NETTOYAGE PRÉALABLE
─────────────────────────────────────────────
Vérifie la présence de chaque fichier avant
de recommander une action :

- all_backtest_trades_export.csv :
  présent ? → archiver dans docs/archived/ ?
- all_backtest_trades_export.meta.json :
  présent ? → archiver dans docs/archived/ ?
- check_state.py :
  présent ? → déplacer vers tools/ ?
- run_trading_bot.ps1 :
  présent ? → déplacer vers scripts/ ?
- setup.py vs pyproject.toml :
  les deux présents ? → lequel conserver ?

Pour chaque action recommandée :
afficher GO [action] pour confirmer ou SKIP.

─────────────────────────────────────────────
ÉTAPE 2 — ARBORESCENCE CIBLE
─────────────────────────────────────────────
Propose l'arborescence complète restructurée
en distinguant ce qui existe déjà de ce qui
sera créé :

MULTI_ASSETS/
├── .claude/
│   ├── context.md          ✅/⚠️/❌
│   └── rules.md            ✅/⚠️/❌
├── .github/
│   └── copilot-instructions.md  ✅/⚠️/❌
├── architecture/
│   ├── system_design.md    ✅/⚠️/❌
│   └── decisions.md        ✅/⚠️/❌
├── knowledge/
│   ├── binance_constraints.md   ✅/⚠️/❌
│   └── trading_constraints.md   ✅/⚠️/❌
├── agents/
│   ├── quant_engineer.md   ✅/⚠️/❌
│   ├── risk_manager.md     ✅/⚠️/❌
│   └── code_auditor.md     ✅/⚠️/❌
├── tasks/               ← existant
├── src/                 ← existant
├── tests/               ← existant
├── docs/                ← existant
├── tools/               ← existant
└── scripts/             ← nouveau si absent

─────────────────────────────────────────────
ÉTAPE 3 — CRÉATION ET MISE À JOUR DES FICHIERS
─────────────────────────────────────────────
Traite chaque fichier dans l'ordre de priorité :
  1. Fichiers ❌ ABSENTS → créer entièrement
  2. Fichiers ⚠️ PARTIELS → compléter les sections
     manquantes sans écraser ce qui est correct
  3. Fichiers ✅ EXISTANTS → vérifier si mise
     à jour nécessaire (contenu obsolète ?)

Pour chaque fichier, avant d'agir :
  - Affiche : "── [nom fichier] ──────────────"
  - Indique : CRÉATION / COMPLÉTION / MISE À JOUR
  - Pour COMPLÉTION : montre uniquement les
    sections à ajouter (pas le fichier entier)
  - Pour MISE À JOUR : montre le diff précis
  - Attends GO avant d'appliquer

Contenu à générer basé sur le code réel :

1. .github/copilot-instructions.md
   Stack · modules src/ · conventions critiques
   (thread-safety, fees figés, stop-loss,
   TRAILING_STOP_MARKET interdit sur Spot,
   credentials) · interdictions absolues ·
   commandes de validation

2. .claude/rules.md
   Règles de modification · ordre de priorité
   (capital > thread-safety > état > backtest) ·
   obligations post-modification · interdictions

3. .claude/context.md
   Pipeline complet depuis le code ·
   table des modules avec responsabilité réelle ·
   contraintes Binance Spot extraites du code ·
   paramètres clés (OOS gates, fees, sizing) ·
   ce qui ne doit pas changer sans benchmark

4. architecture/decisions.md
   ADR pour : BotStateManager + RLock ·
   BACKTEST_TAKER_FEE figé · trailing stop manuel ·
   STOP_LOSS_LIMIT exchange-natif · JSON+HMAC état ·
   PM2 + watchdog · mode démo/production auto

5. knowledge/binance_constraints.md
   Types d'ordres Spot disponibles ·
   rate limits · recvWindow · offset timestamp ·
   filtres LOT_SIZE / MIN_NOTIONAL · fees ·
   idempotence origClientOrderId

6. agents/quant_engineer.md
   Checklist anti-biais backtest spécifique
   MULTI_ASSETS (fees figés, expanding window,
   shift(1) MTF, WF_SCENARIOS source unique)

7. agents/risk_manager.md
   Séquence protection capital MULTI_ASSETS ·
   scénarios de risque avec protection attendue

8. agents/code_auditor.md
   Checklist concurrence (RLock, pair locks) ·
   checklist sécurité Binance ·
   checklist gestion erreurs silencieuses

─────────────────────────────────────────────
ÉTAPE 4 — PLAN DE MIGRATION PRIORISÉ
─────────────────────────────────────────────
Tableau final tenant compte de l'état des lieux :

| Priorité | Fichier | Statut | Effort | % Auto | Impact session |
|----------|---------|--------|--------|--------|----------------|
| 1 | .github/copilot-instructions.md | ❌/⚠️/✅ | Xmin | X% | [impact] |
| 2 | .claude/rules.md | ❌/⚠️/✅ | Xmin | X% | [impact] |
...

Affiche en conclusion :
"✅ File Engineering terminé.
 Créés : [X] · Complétés : [X] · Mis à jour : [X]
 Inchangés : [X] (déjà conformes)"

─────────────────────────────────────────────
SORTIE
─────────────────────────────────────────────
Crée le fichier `tasks/audits/audit_ai_driven_multi_assets.md` avec
l'intégralité du plan structuré en Markdown.
Ne pas afficher le contenu dans le chat — écrire directement le fichier.