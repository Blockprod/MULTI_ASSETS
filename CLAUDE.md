# CLAUDE.md — MULTI_ASSETS Agent Behaviour

> Fichiers complémentaires à lire en priorité :
> - `.claude/context.md` — pipeline, modules, contraintes exchange, état persisté
> - `.claude/rules.md` — interdictions absolues, obligations, règles de déploiement
>
> Ce fichier couvre uniquement **le comportement agent** (workflow, vérification, auto-correction).

---

## Workflow Orchestration

### 1. Plan Mode Default
- Entrer en mode plan pour TOUTE tâche non-triviale (≥ 3 étapes ou décision architecturale)
- Si le plan change en cours de route : STOP, re-planifier avant de continuer
- Utiliser le mode plan pour la vérification, pas seulement pour l'implémentation
- Écrire des specs détaillées en amont pour réduire l'ambiguïté

### 2. Subagent Strategy
- Utiliser des subagents pour les tâches d'exploration longues (ex: lire un orchestrateur de 3400 lignes)
- Garder la fenêtre de contexte principale propre — offloader l'analyse parallèle
- Pour les problèmes complexes, lancer plusieurs subagents en parallèle
- Une tâche par subagent pour une exécution focalisée

### 3. Self-Improvement Loop
- **Lire `tasks/lessons.md` au début de chaque session sur ce projet**
- Après toute correction de l'utilisateur : mettre à jour `tasks/lessons.md` avec le pattern
- Itérer sans relâche sur ces leçons jusqu'à ce que le taux d'erreur baisse
- Ne pas répéter une erreur déjà documentée dans `tasks/lessons.md`

### 4. Verification Before Done
- Ne jamais marquer une tâche comme complète sans preuve que ça fonctionne
- Validation systématique après chaque `.py` modifié :
  ```powershell
  .venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/<fichier>.py').read()); print('OK')"
  pytest tests/ -x -q
  ```
- Se demander : "Un staff engineer validerait-il cette PR ?"
- Jamais `git commit --no-verify`, jamais bypass de `_bot_state_lock`
- Jamais modifier `backtest_taker_fee` comme contournement d'un test qui échoue

### 5. Demand Elegance (Balanced)
- Pour les tâches non-triviales : "Y a-t-il une approche plus élégante ?"
- Si un fix semble hacky : "Avec tout ce que je sais maintenant, quelle est la solution correcte ?"
- Ne pas sur-appliquer — pour une correction évidente, ne pas sur-ingéniérer
- Sur ce projet : un hack sur `safe_market_buy` ou la chaîne BUY→SL peut engager du capital réel

### 6. Autonomous Bug Fixing
- Quand un bug est rapporté ou observé : le corriger directement, sans demander de prise en main
- Pointer les logs, erreurs, tests qui échouent — puis les résoudre
- Zéro changement de contexte requis de la part de l'utilisateur
- Corriger les CI failures sans attendre d'être demandé (Ruff, Pyright, pytest)

---

## Task Management

1. **Plan First** : écrire le plan avec des items cochables avant d'implémenter
2. **Verify Plan** : vérifier avant de commencer l'implémentation
3. **Track Progress** : marquer les items comme complétés au fur et à mesure
4. **Explain Changes** : résumé haut niveau après chaque étape
5. **Document Results** : section revue dans le plan
6. **Capture Lessons** : mettre à jour `tasks/lessons.md` après toute correction

---

## Core Principles

- **Simplicity First** : chaque changement doit être le plus simple possible. Impact minimal sur le code.
- **No Laziness** : trouver les causes racines. Pas de fix temporaires. Standards senior developer.
- **Minimal Impact** : les changements ne touchent que ce qui est nécessaire. Ne pas introduire de bugs.
