---
modele: sonnet-4.6
mode: agent
contexte: codebase
produit: AUDIT_STRATEGIQUE_MULTI_ASSETS.md
derniere_revision: 2026-03-18
---

#codebase

Tu es un Quantitative Researcher spécialisé en trading
algorithmique crypto trend-following.
Tu réalises un audit EXCLUSIVEMENT stratégique
sur MULTI_ASSETS.

─────────────────────────────────────────────
ÉTAPE 0 — VÉRIFICATION PRÉALABLE (OBLIGATOIRE)
─────────────────────────────────────────────
Vérifie si ce fichier existe déjà dans :
  tasks/audits/AUDIT_STRATEGIQUE_MULTI_ASSETS.md

Si trouvé, affiche :
"⚠️ Audit stratégique existant détecté :
 Fichier : tasks/audits/AUDIT_STRATEGIQUE_MULTI_ASSETS.md
 Date    : [date modification]
 Lignes  : [nombre approximatif]

 [NOUVEAU]  → audit complet (écrase l'existant)
 [MÀJOUR]   → compléter sections manquantes
 [ANNULER]  → abandonner"

Si absent → démarrer directement :
"✅ Aucun audit stratégique existant. Démarrage..."

─────────────────────────────────────────────
PÉRIMÈTRE STRICT
─────────────────────────────────────────────
Tu analyses UNIQUEMENT :
- Validité statistique des signaux et backtests
- Robustesse du walk-forward
- Cohérence backtest ↔ live
- Qualité du risk management financier

Tu n'analyses PAS la sécurité, la concurrence,
l'organisation des modules ou le code Python.

─────────────────────────────────────────────
CONTRAINTES ABSOLUES
─────────────────────────────────────────────
- Ne lis aucun fichier .md, .txt, .rst, .csv
- Cite fichier:ligne pour chaque point
- Conclus chaque sous-point par
  CONFORME / NON CONFORME / À VÉRIFIER
- Écris "À VÉRIFIER" sans preuve dans le code

─────────────────────────────────────────────
BLOC 1 — INTÉGRITÉ STATISTIQUE DES SIGNAUX
─────────────────────────────────────────────
Analyse src/strategy/, src/data/ :

1.1 Biais look-ahead
    - Indicateurs calculés en expanding window
      ou sur dataset complet avant la boucle ?
    - MTF filter 4h utilise shift(1) ?
    - Signaux utilisent iloc[-2] ou iloc[-1] ?
    - start_date dynamique via _fresh_start_date()
      ou figée à l'import ?

1.2 Frais et reproductibilité
    - backtest_taker_fee et backtest_maker_fee
      FIGÉS et jamais écrasés par API Binance ?
    - Même taux fee backtest et live ?
    - Slippage modélisé en backtest ?

1.3 Walk-forward
    - Paramètres ré-optimisés sur IS uniquement ?
    - Résultat final OOS-validé ou best full-sample ?
    - OOS gates : Sharpe ≥ 0.8, WinRate ≥ 30%,
      decay ≥ 0.15 — appliqués correctement ?

1.4 Cohérence backtest ↔ live
    - Mêmes fonctions de signal backtest et live ?
    - Filtres actifs en backtest désactivés live ?
    - WF_SCENARIOS : source unique ou dupliquée ?

─────────────────────────────────────────────
BLOC 2 — SOLIDITÉ DE LA STRATÉGIE
─────────────────────────────────────────────
Analyse src/strategy/ :

- Les 4 scénarios WF_SCENARIOS cohérents
  backtest ↔ live ?
- StochRSI calculé identiquement partout ?
- Les 3 modes de sizing cohérents
  backtest ↔ live ?
- compute_position_size retourne 0 si ATR = 0 ?
- Partiels (+2%, +4%) cohérents backtest ↔ live ?
- Trailing stop manuel correct avec max_price ?
- Re-placement stop après chaque partial ?

─────────────────────────────────────────────
BLOC 3 — RISK MANAGEMENT FINANCIER
─────────────────────────────────────────────
Analyse src/core/, src/trading/ :

- daily_loss_limit_pct reset journalier correct ?
- _update_daily_pnl() après chaque vente ?
- drawdown kill-switch persisté au redémarrage ?
- oos_blocked persisté au redémarrage ?
- emergency_halt vérifié début de chaque cycle ?
- Corrélation guard implémenté correctement ?

─────────────────────────────────────────────
SORTIE OBLIGATOIRE
─────────────────────────────────────────────
Crée le fichier :
  tasks/audits/audit_strategique_multi_assets.md
Crée le dossier tasks/audits/ s'il n'existe pas.

Structure du fichier :
## BLOC 1 — INTÉGRITÉ STATISTIQUE
## BLOC 2 — SOLIDITÉ STRATÉGIE
## BLOC 3 — RISK MANAGEMENT
## SYNTHÈSE

Tableau synthèse :
| ID | Bloc | Description | Fichier:Ligne |
| Sévérité | Impact Perf | Effort |

Sévérité P0/P1/P2/P3.
Top 3 biais qui invalident le backtest.
Points du modèle statistique à conserver.

Confirme dans le chat :
"✅ tasks/audits/audit_strategique_multi_assets.md créé
 🔴 X · 🟠 X · 🟡 X"





