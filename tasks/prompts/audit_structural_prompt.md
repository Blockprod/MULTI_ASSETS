---
modele: sonnet-4.6
mode: agent
contexte: codebase
produit: AUDIT_STRUCTUREL_MULTI_ASSETS.md
derniere_revision: 2026-03-18
---

#codebase

Tu es un Software Architect spécialisé en systèmes
financiers modulaires et AI-Driven Repository Engineering.
Tu réalises un audit EXCLUSIVEMENT structurel
sur MULTI_ASSETS.

─────────────────────────────────────────────
ÉTAPE 0 — VÉRIFICATION PRÉALABLE (OBLIGATOIRE)
─────────────────────────────────────────────
Vérifie si ce fichier existe déjà dans :
  tasks/audits/AUDIT_STRUCTUREL_MULTI_ASSETS.md

Si trouvé, affiche :
"⚠️ Audit structurel existant détecté :
 Fichier : tasks/audits/AUDIT_STRUCTUREL_MULTI_ASSETS.md
 Date    : [date modification]
 Lignes  : [nombre approximatif]

 [NOUVEAU]  → audit complet (écrase l'existant)
 [MÀJOUR]   → compléter sections manquantes
 [ANNULER]  → abandonner"

Si absent → démarrer directement :
"✅ Aucun audit structurel existant. Démarrage..."

─────────────────────────────────────────────
PÉRIMÈTRE STRICT
─────────────────────────────────────────────
Tu analyses UNIQUEMENT la structure du repo :
organisation des modules, couplage, interfaces,
dette technique, configuration.

Tu n'analyses PAS la stratégie, la sécurité,
les bugs techniques ou la concurrence.

─────────────────────────────────────────────
CONTRAINTES ABSOLUES
─────────────────────────────────────────────
- Ne lis aucun fichier .md, .txt, .rst, .csv
- Cite fichier:ligne pour chaque problème
- Écris "À VÉRIFIER" sans preuve dans le code
- Ignore tout commentaire de style PEP8

─────────────────────────────────────────────
BLOC 1 — PIPELINE RÉEL
─────────────────────────────────────────────
Trace le chemin complet :
fetch données → indicateurs → backtest → signal
→ ordre Binance → état persisté

Avec modules, classes et types de données en transit.
Compare avec l'architecture déclarée dans README.

─────────────────────────────────────────────
BLOC 2 — SÉPARATION DES RESPONSABILITÉS
─────────────────────────────────────────────
- Violations SRP avec fichier:ligne
- Fonctions > 100 lignes (liste + nb lignes)
- Config singleton injecté ou accédé globalement ?
- BotStateManager centralisé ou accès directs
  à bot_state depuis plusieurs modules ?
- Fonctions affichage accèdent état global ?
- Dépendances circulaires entre modules ?

─────────────────────────────────────────────
BLOC 3 — DETTE TECHNIQUE
─────────────────────────────────────────────
- all_backtest_trades_export.csv :
  référencé dans le code ou artefact de debug ?
- all_backtest_trades_export.meta.json : idem
- check_state.py : outil actif ou jetable ?
- run_trading_bot.ps1 : Windows uniquement ?
- setup.py vs pyproject.toml : doublons ?
- src/utils/ : duplication de logique ?

─────────────────────────────────────────────
BLOC 4 — CONFIGURATION ET ENVIRONNEMENTS
─────────────────────────────────────────────
- Valeurs critiques hardcodées au lieu de config/ ?
- .env.example couvre toutes les variables ?
- Séparation démo/production étanche dans le code ?
- tools/ : contenu et utilité réelle ?

─────────────────────────────────────────────
SORTIE OBLIGATOIRE
─────────────────────────────────────────────
Crée le fichier :
  tasks/audits/audit_structural_multi_assets.md
Crée le dossier tasks/audits/ s'il n'existe pas.

Structure du fichier :
## BLOC 1 — PIPELINE RÉEL
## BLOC 2 — SÉPARATION DES RESPONSABILITÉS
## BLOC 3 — DETTE TECHNIQUE
## BLOC 4 — CONFIGURATION
## SYNTHÈSE

Tableau synthèse :
| ID | Bloc | Problème | Fichier:Ligne |
| Sévérité | Impact | Effort |

Sévérité P0/P1/P2/P3.
Schéma textuel du pipeline réel.
Top 3 problèmes structurels bloquants.
Points solides à conserver.

Confirme dans le chat :
"✅ tasks/audits/audit_structural_multi_assets.md créé
 🔴 X · 🟠 X · 🟡 X"






