# Template Correction P0 — MULTI_ASSETS

## Procédure standard pour une correction P0

### 1. Identification
```
Fichier(s) concerné(s) :
Lignes :
Symptôme :
Impact production :
Dépendances (autres P0 à corriger avant) :
```

### 2. Lecture du code avant modification
- Lire le fichier complet (ou au minimum 50 lignes autour du problème)
- Identifier toutes les références au code concerné (grep dans le workspace)
- Vérifier s'il existe un test existant qui couvre ce chemin

### 3. Correction
Respecter le contrat du fichier `.claude/rules.md` :
- Thread-safety si `bot_state` est touché
- Idempotence si un ordre exchange est placé
- `force=True` sur `save_bot_state()` si position change
- `try/except` explicite (pas de decorator silencieux sur les chemins critiques)

### 4. Validation obligatoire
```powershell
# Syntaxe
.venv\Scripts\python.exe -c "import ast; ast.parse(open('code/src/<fichier>.py').read()); print('OK')"
# Tests complets
pytest tests/ -x -q
# Si nouveau test créé :
pytest tests/test_<nouveau>.py -v
```

### 5. Checklist de livraison P0
- [ ] Code ne lève plus le bug décrit
- [ ] Cas nominal toujours fonctionnel (test existant passe)
- [ ] Nouveau test créé pour le cas d'erreur (non-régression)
- [ ] `bot_state` cohérent après la correction (pas d'état fantôme)
- [ ] Email d'alerte envoyé au bon niveau (CRITICAL, WARNING, INFO)
- [ ] Aucun log de credential introduit

### 6. Exemples de patterns P0 déjà appliqués dans le projet
| P0 | Pattern appliqué |
|----|-----------------|
| P0-01 (SL non garanti) | `OrderError` + `safe_market_sell` d'urgence + `sl_exchange_placed` persisté |
| P0-02 (balance 0 silencieux) | `BalanceUnavailableError` → cycle sauté, pas de buy avec balance=0 |
| P0-03 (OOS gate bypassé) | `oos_blocked` persisté au redémarrage (C-05), pas purgé par load |
| P0-04 (HMAC hardcodé) | Clé HMAC = BINANCE_SECRET_KEY, EnvironmentError si absente |
| P0-05 (SizingError silencieuse) | `SizingError` levée proprement, catchée dans `_execute_buy()` |
| P0-SAVE (save silencieux) | 3 failures → emergency_halt, alerte CRITICAL email |
