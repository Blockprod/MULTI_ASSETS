# MULTI_ASSETS — Règles Claude

## Règles de modification du code

### INTERDICTIONS ABSOLUES
1. **`except Exception: pass`** — toujours logger au minimum `logger.debug("[TAG] msg: %s", e)`
2. **Credentials en clair** — `config.api_key`, `config.secret_key` ne doivent jamais
   apparaître dans des logs, prints, ou f-strings non contrôlés
3. **`backtest_taker_fee` / `backtest_maker_fee` mutés au runtime** — ces valeurs sont le
   "golden standard" de reproductibilité ; seule une modification accompagnée d'un nouveau
   benchmark est acceptable
4. **Variable `start_date` figée à l'import** — utiliser `_fresh_start_date()`
5. **`TRAILING_STOP_MARKET` sur Spot** — ce type n'existe pas, lève `NotImplementedError`
6. **Accès à `bot_state[pair]` en écriture sans `with _bot_state_lock:`**
7. **BUY sans placement immédiat du stop-loss** — la chaîne achat→SL est atomique

### OBLIGATIONS
1. Après chaque modification de fichier `.py` : valider la syntaxe avec
   `python -c "import ast; ast.parse(open('...').read())"`
2. Après une correction de bug : lancer `pytest tests/ -x -q`
3. Pour une nouvelle feature : proposer un test dans `tests/` avant ou en même temps
4. Pour tout changement dans `bot_config.py` : vérifier la rétrocompatibilité de
   `Config.from_env()` (les clés env sont fixées par le `.env` de prod)
5. Pour tout changement dans `state_manager.py` : vérifier `_KNOWN_PAIR_KEYS` et
   `_KNOWN_GLOBAL_KEYS` — ajouter les nouvelles clés dans ces sets

## Règles de réponse

### Ordre de priorité lors d'un conflit
1. **Sécurité du capital** (stop-loss garanti, emergency halt, daily limit)
2. **Thread-safety** (verrous corrects)
3. **Intégrité de l'état** (HMAC, idempotence)
4. **Reproductibilité du backtest** (fees figés, pas de look-ahead)
5. Qualité de code (lisibilité, DRY)

### Niveau de changement
- Ne modifier que ce qui est explicitement demandé
- Ne pas refactorer du code fonctionnel adjacent à un bug fix
- Ne pas ajouter de docstrings ou type annotations sur du code non touché
- Ne pas créer de fichiers Markdown pour documenter chaque changement

### Format de diff proposé
Toujours montrer : fichier, lignes concernées, ancien code → nouveau code.
Inclure 3-5 lignes de contexte autour du changement.

## Règles de test

### Quoi tester
- Toute fonction qui touche `bot_state`, `save_bot_state`, `load_bot_state`
- Toute fonction qui appelle `safe_market_buy` ou `safe_market_sell`
- Toute logique de protection du capital (daily limit, oos_blocked, emergency_halt)
- Toute sérialisation/désérialisation d'état

### Conventions de test
- Mocker `BinanceFinalClient` avec `pytest-mock` — jamais d'appel API réel en test
- Utiliser des `tmp_path` pytest pour les fichiers d'état temporaires
- Les tests de state doivent vérifier l'intégrité HMAC post-sauvegarde

## Règles de déploiement (ne pas faire sans confirmation)
- `git push` ou modification de `config/ecosystem.config.js`
- Modification du `.env` de production
- `pm2 restart` ou `pm2 delete`
- Modification de `states/bot_state.json` directement
