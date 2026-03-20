#codebase

Tu es un Senior Quantitative Engineer spécialisé en
systèmes de trading algorithmique augmentés par l'IA,
avec une expérience concrète en déploiement de modèles
ML en production sur marchés crypto.

─────────────────────────────────────────────
MISSION
─────────────────────────────────────────────
Évaluer si l'intégration d'agents IA et/ou de
Machine Learning dans MULTI_ASSETS est pertinente,
intelligente et réaliste — en te basant UNIQUEMENT
sur ce qui est déjà en place dans le code.

Ce n'est PAS un exercice théorique.
Chaque recommandation doit être justifiée par
un gain mesurable sur un système de trading réel
en production sur Binance Spot.

─────────────────────────────────────────────
CONTRAINTES ABSOLUES
─────────────────────────────────────────────
- Lis le code source réel avant toute conclusion
- Ne lis aucun fichier .md, .txt, .rst
- Cite fichier:ligne pour chaque observation
- Sois factuel et direct — zéro enthousiasme gratuit
- Si une idée est techniquement séduisante mais
  dangereuse en production réelle : dis-le clairement
- Ton verdict final doit être binaire :
  PERTINENT / NON PERTINENT pour chaque cas

─────────────────────────────────────────────
PHASE 1 — DIAGNOSTIC DE L'EXISTANT
─────────────────────────────────────────────
Avant toute recommandation, analyse ce qui est
déjà en place dans le code :

1.1 Signaux et stratégie actuels
    - Quels indicateurs techniques sont utilisés ?
      (EMA, RSI, StochRSI, ADX, TRIX, ATR...)
    - Comment les signaux buy/sell sont-ils générés ?
    - Quelle est la logique de sélection des paramètres
      (walk-forward, OOS gates) ?
    - Y a-t-il déjà une forme de régime de marché
      détectée ? (MTF filter, conditions de marché)

1.2 Données disponibles
    - Quelles données sont collectées et persistées ?
      (OHLCV, trades, états bot, journal JSONL)
    - Quelle est la granularité temporelle ?
    - Quelle est la profondeur historique disponible ?
    - Y a-t-il des données de performance par paire ?

1.3 Infrastructure et contraintes techniques
    - Stack actuel : Python 3.11.9, Cython, PM2, Windows
    - Contraintes temps réel : cycle de 2 minutes
    - Contraintes ressources : CPU/RAM disponibles
    - Contraintes Binance : rate limits, latence

1.4 Points de décision dans le pipeline actuel
    Identifie TOUS les endroits dans le code où
    une décision est prise de façon déterministe
    et qui pourrait bénéficier d'un modèle adaptatif :
    - Sélection des paramètres EMA
    - Seuils d'entrée/sortie StochRSI
    - Sizing des positions
    - Gestion des stops
    - Sélection des paires à trader

Livrable Phase 1 : carte complète des points de
décision avec fichier:ligne pour chaque point.

─────────────────────────────────────────────
PHASE 2 — ÉVALUATION DES OPPORTUNITÉS IA/ML
─────────────────────────────────────────────
Pour chaque opportunité ci-dessous, évalue sa
pertinence sur ce projet spécifique.

Critères d'évaluation pour chaque opportunité :
  - Gain attendu mesurable (Sharpe, WinRate, DD)
  - Complexité d'implémentation (XS/S/M/L)
  - Risque d'introduction en production
  - Compatibilité avec le cycle de 2 minutes
  - Données disponibles suffisantes (oui/non)
  - Verdict : PERTINENT / NON PERTINENT + pourquoi

2.1 Machine Learning sur les signaux

  A. Remplacement ou enrichissement des signaux
     techniques actuels (EMA/StochRSI) par un modèle
     supervisé (Random Forest, XGBoost, LightGBM)
     → Est-ce que les features disponibles (OHLCV +
     indicateurs) sont suffisantes pour entraîner
     un modèle performant en crypto trend-following ?
     → Risque de surapprentissage sur crypto ?

  B. Détection de régime de marché par ML
     (clustering K-Means ou HMM sur volatilité,
     volume, corrélation inter-paires)
     → Peut-on améliorer la logique MTF filter
     actuelle avec un modèle de régimes ?

  C. Optimisation adaptative des seuils
     (seuils StochRSI, OOS gates) par reinforcement
     learning ou optimisation bayésienne
     → Est-ce réaliste avec la fréquence actuelle
     des trades et la taille du dataset ?

  D. Prédiction du sizing optimal
     par régression sur les conditions de marché
     → Les 3 modes de sizing actuels (risk, fixed,
     volatility_parity) sont-ils améliorables par ML ?

2.2 Agents IA autonomes

  E. Agent de sélection de paires
     Un agent qui sélectionne dynamiquement les
     meilleures paires à trader selon les conditions
     de marché actuelles, au lieu d'une liste fixe
     → Données disponibles suffisantes ?
     → Risque de surconcentration ou sur-trading ?

  F. Agent de gestion des stops
     Un agent qui ajuste dynamiquement les niveaux
     de stop-loss selon la volatilité réalisée
     et le régime de marché
     → Compatible avec STOP_LOSS_LIMIT exchange-natif ?
     → Risque d'interaction avec le stop exchange ?

  G. Agent de rebalancing du portefeuille
     Un agent qui gère l'allocation du capital
     entre les paires selon les performances
     récentes et les corrélations
     → Améliore-t-il réellement le risk-adjusted
     return vs le sizing actuel ?

  H. Agent LLM pour l'analyse de sentiment
     Utilisation d'un LLM (GPT, Claude) pour
     analyser le sentiment marché (news, social)
     et filtrer les signaux techniques
     → Latence compatible avec le cycle 2 minutes ?
     → Fiabilité sur marchés crypto ?

2.3 Amélioration du backtest par ML

  I. Walk-forward adaptatif
     Ajustement automatique des fenêtres IS/OOS
     selon les conditions de marché détectées
     → Améliore-t-il la stabilité OOS ?

  J. Sélection des features par importance
     (SHAP values sur les indicateurs existants)
     pour identifier quels indicateurs apportent
     vraiment de la valeur vs du bruit
     → Peut simplifier la stratégie actuelle ?

─────────────────────────────────────────────
PHASE 3 — RECOMMANDATION FINALE
─────────────────────────────────────────────
3.1 Tableau de décision

| ID | Opportunité | Verdict | Gain estimé |
|    |             |         | Complexité  |
|    |             |         | Risque prod |
|----|-------------|---------|-------------|
| A  | [titre]     | ✅/❌   | [métriques] |

3.2 Roadmap recommandée

Si des opportunités sont PERTINENTES, propose
une séquence d'implémentation réaliste :

NIVEAU 1 — Sans risque pour la production
  (peut tourner en parallèle du bot actuel,
   en mode observation uniquement)

NIVEAU 2 — Intégration progressive
  (paper trading d'abord, validation OOS,
   puis production avec capital réduit)

NIVEAU 3 — Remplacement de composants existants
  (uniquement si NIVEAU 2 validé sur 30+ jours)

3.3 Ce qu'il ne faut PAS faire

Liste explicite des intégrations IA/ML à éviter
sur ce projet spécifique, avec justification :
- Trop complexe pour le gain attendu
- Risque de régression sur des mécanismes
  qui fonctionnent déjà en production
- Incompatible avec les contraintes Binance Spot

3.4 Verdict global

En 5 lignes maximum :
- Faut-il intégrer de l'IA/ML sur MULTI_ASSETS ?
- Si oui, par quoi commencer et pourquoi ?
- Quel est le risque principal à surveiller ?

─────────────────────────────────────────────
FORMAT DE SORTIE
─────────────────────────────────────────────
Crée le fichier :
  tasks/audits/audit_IA_ML_multi_assets.md
Crée le dossier tasks/audits/ s'il n'existe pas.

Structure obligatoire du fichier :

# AUDIT IA & ML — MULTI_ASSETS — [DATE]

## PHASE 1 — Diagnostic de l'existant
## PHASE 2 — Évaluation des opportunités
## PHASE 3 — Recommandation finale

Règles de contenu :
- Tableaux Markdown pour les synthèses
- Verdict PERTINENT ✅ / NON PERTINENT ❌
  pour chaque opportunité
- Exemples de code uniquement si nécessaire
  pour illustrer une intégration concrète
- Aucun enthousiasme gratuit — seuls les
  gains mesurables comptent

Confirme dans le chat uniquement :
"✅ tasks/audits/audit_IA_ML_multi_assets.md créé
 ✅ PERTINENT : X opportunités
 ❌ NON PERTINENT : X opportunités
 👉 Opportunité prioritaire : [titre]"