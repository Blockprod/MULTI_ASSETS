# Plan d'Action — StochRSI & OOS ONDO

> creation: 2026-05-03 à 14:39
> statut: **TERMINÉ**
> priorité: P0 → P2
> source: analyse log_rsi.md + log_rsi copy.md (session 03 mai 2026)

---

## Résumé

L'analyse croisée des logs live et du redémarrage post-fix a révélé **5 anomalies**,
dont 2 déjà corrigées et 3 restant à traiter. Le problème structurel principal est
l'insuffisance d'historique ONDO (paire listée ~avril 2025) qui bloque le bot
indéfiniment via `oos_blocked=True`.

---

## CONFIRMÉ — Corrections déjà appliquées

### ✅ FIX-1 — Bug NaN/0.5 dans `compute_stochrsi()`

**Symptôme** : pendant le warm-up (13 premières lignes post-RSI), `denom=NaN` mais
`np.where(denom != 0, ...)` évaluait `NaN != 0` comme `False` → retournait `0.5`
au lieu de `NaN`. Ces lignes survivaient à `dropna()` avec un faux signal neutre.

**Preuve de fix** : log post-correction montre `375 bars` au lieu de `388`
(388 - 375 = 13 lignes correctement expulsées = warm-up RSI 14 + StochRSI 14).

**Fichier** : `code/src/indicators_engine.py` — `compute_stochrsi()`

- [x] Fix appliqué : `np.where(np.isnan(denom), np.nan, np.where(denom > 0, ...))`
- [x] Commentaire erroné corrigé
- [x] 249 tests passés post-fix

---

### ✅ FIX-2 — Seuil `stoch_rsi_buy_max` hardcodé dans `display_ui.py`

**Symptôme** : `signal_generator.py` lisait `config.stoch_rsi_buy_max` (dynamique)
mais `display_ui.py` hardcodait `0.8` → désynchronisation si la config est modifiée.

**Fichier** : `code/src/display_ui.py` — panneau "SIGNAL D'ACHAT"

- [x] Fix appliqué : `_buy_max = getattr(config, 'stoch_rsi_buy_max', 0.8)` + label dynamique
- [x] 249 tests passés post-fix

---

## P0 — Bot bloqué en production (`oos_blocked=True`)

### Cause racine

ONDO a été listé sur Binance ~avril 2025 → **375 bars 1d disponibles** alors que
le WF nécessite ≥ 700 bars pour la résolution 1d. Aucune config ne passera les OOS
gates (Sharpe ≥ 0.8, WR ≥ 30%) sur un historique aussi court. Le bot restera en
mode conservatif bloqué jusqu'à ~avril 2027 sur cette paire seule.

### Option A — Ajouter une paire avec historique suffisant (recommandé)

Configurer une seconde paire (BTC, ETH, SOL, BNB) avec ≥ 700 bougies 1d (>2 ans)
qui pourra valider le WF et générer des signaux actifs.

- [ ] Identifier une paire USDC avec historique ≥ 700 bougies 1d sur Binance
- [ ] Ajouter la paire dans `MULTI_SYMBOLS.py` (liste `PAIRS`)
- [ ] Vérifier que le capital est correctement alloué entre les paires
- [ ] Valider que `_bot_state_lock` couvre bien la nouvelle paire

### Option B — Proxy WF 4h → 1d (résolution dégradée acceptable)

Quand la résolution 1d n'a pas assez de données, utiliser les résultats WF 4h
comme proxy de validation. Les configs 4h ont 2323 bars (largement > 700).

⚠️ Risque : les configs 4h overfittent aussi (OOS Sharpe=-18). Cette option ne
résout pas l'overfit, elle contourne seulement le verrou WF.

- [ ] Analyser pourquoi 4h OOS Sharpe=-18 (look-ahead ? suroptimisation ?)
- [ ] Si overfit confirmé sur 4h : rejeter cette option
- [ ] Si 4h OOS peut être amélioré : implémenter le proxy dans `walk_forward.py`

### Option C — Mode "paire récente" avec gates relaxées

Détecter automatiquement les paires avec < 700 bars 1d et appliquer des gates
OOS allégées (Sharpe ≥ 0.5, WR ≥ 25%) avec un avertissement capital réduit.

⚠️ Risque : augmente le risque de trading sur stratégies non validées.

- [ ] Décision architecture : accepter le risque ou attendre ?
- [ ] Si oui : ajouter paramètre `min_oos_sharpe_short_history` dans `bot_config.py`

---

## P1 — Filtre `min_trades` dans le classement IS

**Symptôme** : `StochRSI_ADX 1d` apparaît en positions 13-15 avec `4 trades / 100% WR`.
Un WR de 100% sur 4 trades est statistiquement non significatif et peut induire
en erreur lors de la sélection de la meilleure config IS.

**Fichier** : `code/src/backtest_orchestrator.py` ou `backtest_runner.py`
(section tri/affichage Top 15)

- [ ] Localiser le tri IS (probablement tri par profit décroissant)
- [ ] Ajouter un filtre secondaire : configs avec `nb_trades < 10` marquées ⚠️ dans l'affichage
- [ ] Optionnel : exclure ces configs du classement principal (colonne séparée "trop peu de trades")
- [ ] Tests : vérifier qu'aucun test existant ne dépend du rang de ces configs

---

## P2 — Offset horloge persistant (-2179ms)

**Symptôme** : `SYNCHRO OK: offset=-2179ms` à chaque démarrage. L'horloge Windows
est en retard de ~2.2 secondes. Actuellement couvert par `recvWindow=60000ms` mais
un drift croissant pourrait causer des erreurs `-1021 INVALID_TIMESTAMP`.

- [ ] Vérifier si l'offset est stable ou croissant sur plusieurs jours de logs
- [ ] Si croissant : déclencher `w32tm /resync /force` automatiquement au démarrage
  quand `|offset| > 3000ms` (seuil sécuritaire avant les 5000ms de Binance)
- [ ] Localiser le module de sync timestamp (`timestamp_utils.py`) et ajouter l'appel

---

## Calendrier

| ID | Priorité | Statut | Dépendance |
|---|---|---|---|
| FIX-1 NaN compute_stochrsi | P0 | ✅ CONFIRMÉ | — |
| FIX-2 display_ui hardcodé | P2 | ✅ CONFIRMÉ | — |
| P0-A Nouvelle paire (PEPE) | P0 | ✅ CONFIRMÉ | Switch PEPE ~1095 bars 1d |
| P0-B Proxy WF 4h | P0 | ❌ REJETÉ | Overfit 4h OOS=-18 confirmé |
| P0-C Gates relaxées | P0 | ❌ REJETÉ | Non nécessaire avec PEPE |
| P1 Filtre min_trades ⚠️ | P1 | ✅ CONFIRMÉ | display_ui.py · 797 tests |
| P2 Offset horloge auto-resync | P2 | ✅ CONFIRMÉ | timestamp_utils.py · seuil 3000ms |

---

## Décision requise (utilisateur)

**Sur P0** : quelle option choisir pour débloquer le trading ?
- A (nouvelle paire) → quelle paire ? BTC ? ETH ?
- B (proxy 4h) → nécessite d'abord d'expliquer l'OOS Sharpe=-18 sur 4h
- C (gates relaxées) → risque capital assumé

**Sur P0-B** : l'OOS Sharpe=-18 sur 4h est-il un signal d'alarme (l'algo ne fonctionne
pas sur ONDO) ou un artefact de la fenêtre de test trop courte ?
