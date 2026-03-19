---
modele: sonnet-4.6
mode: agent
contexte: codebase
produit: AUDIT_EMAIL_ALERTS_MULTI_ASSETS.md
derniere_revision: 2026-03-18
---

#codebase

Tu réalises un audit EXCLUSIVEMENT centré sur
le système d'alertes email de MULTI_ASSETS.

─────────────────────────────────────────────
ÉTAPE 0 — VÉRIFICATION PRÉALABLE (OBLIGATOIRE)
─────────────────────────────────────────────
Vérifie si ce fichier existe déjà dans :
  tasks/audits/AUDIT_EMAIL_ALERTS_MULTI_ASSETS.md

Si trouvé, affiche :
"⚠️ Audit email existant détecté :
 Fichier : tasks/audits/AUDIT_EMAIL_ALERTS_MULTI_ASSETS.md
 Date    : [date modification]
 Lignes  : [nombre approximatif]

 [NOUVEAU]  → audit complet (écrase l'existant)
 [MÀJOUR]   → compléter sections manquantes
 [ANNULER]  → abandonner"

Si absent → démarrer directement :
"✅ Aucun audit email existant. Démarrage..."

─────────────────────────────────────────────
PÉRIMÈTRE STRICT
─────────────────────────────────────────────
Tu analyses UNIQUEMENT src/utils/ et tout module
contenant des fonctions d'envoi email ou de
notification. Tu n'analyses PAS le reste.

─────────────────────────────────────────────
CONTRAINTES ABSOLUES
─────────────────────────────────────────────
- Ne lis aucun fichier .md, .txt, .rst
- Cite fichier:ligne pour chaque problème
- Conclus chaque item par COUVERT / NON COUVERT /
  À VÉRIFIER

─────────────────────────────────────────────
BLOC 1 — SYSTÈME D'ENVOI
─────────────────────────────────────────────
- Retry avec backoff sur échec SMTP ?
- Cooldown entre alertes similaires ?
- Transport SMTP TLS port 587 ?
- Échec envoi : loggé sans crasher le bot ?
- GOOGLE_MAIL_PASSWORD depuis env uniquement ?

─────────────────────────────────────────────
BLOC 2 — COUVERTURE DES ÉVÉNEMENTS
─────────────────────────────────────────────
Événements système :
- [ ] Exception critique non gérée
- [ ] Échec save_state 3 fois → emergency_halt
- [ ] Échec connexion API Binance
- [ ] Données OHLCV manquantes ou corrompues
- [ ] Circuit breaker déclenché
- [ ] Watchdog : bot considéré comme hung

Événements trading :
- [ ] BUY exécuté (paire, qty, prix, stop placé)
- [ ] SELL exécuté (paire, raison, PnL)
- [ ] Ordre bloqué (raison explicite)
- [ ] Ordre échoué (timeout, rejet Binance)
- [ ] Stop-loss déclenché
- [ ] Vente partielle exécutée
- [ ] Position ouverte sans stop-loss 🔴

Événements protection capital :
- [ ] daily_loss_limit atteint
- [ ] drawdown kill-switch déclenché
- [ ] oos_blocked activé
- [ ] emergency_halt activé

─────────────────────────────────────────────
BLOC 3 — QUALITÉ DU CONTENU
─────────────────────────────────────────────
- Emails contiennent paire + prix + qty +
  raison + horodatage ?
- Emails erreur incluent traceback ?
- Credential Binance dans le corps ? 🔴
- Sujets distinguent critique vs informatif ?
- Template email centralisé ou dupliqué ?

─────────────────────────────────────────────
BLOC 4 — CAS MANQUANTS ET RISQUES
─────────────────────────────────────────────
- Erreurs critiques swallowées sans notification ?
- Événements trading loggés console sans email ?
- Cascade d'emails identiques possible
  sur retry loop ?
- Bot continue normalement si SMTP échoue ?

─────────────────────────────────────────────
SORTIE OBLIGATOIRE
─────────────────────────────────────────────
Crée le fichier :
  tasks/audits/audit_email_alerts_multi_assets.md
Crée le dossier tasks/audits/ s'il n'existe pas.

Structure du fichier :
## BLOC 1 — SYSTÈME D'ENVOI
## BLOC 2 — COUVERTURE DES ÉVÉNEMENTS
## BLOC 3 — QUALITÉ DU CONTENU
## BLOC 4 — CAS MANQUANTS
## SYNTHÈSE

Tableau synthèse :
| ID | Bloc | Description | Fichier:Ligne |
| Sévérité | Impact | Effort |

Sévérité P0/P1/P2/P3.
Liste événements NON COUVERTS par criticité.
Top 3 risques liés aux alertes manquantes.
Points forts à conserver.

Confirme dans le chat :
"✅ tasks/audits/audit_email_alerts_multi_assets.md créé
 🔴 X · 🟠 X · 🟡 X"

