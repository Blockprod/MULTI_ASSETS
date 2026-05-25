"""
Script one-shot : recalcule le cooldown PEPEUSDC après P3.2.

Contexte :
  - Avant P3.2, le cooldown 1d = 12 candles × 1440 min = 17280 min (12 jours)
  - Après P3.2, le cooldown 1d = 5 candles × 1440 min = 7200 min (5 jours)
  - Le cooldown existant a été calculé avec 12 jours → expire ~2026-06-01
  - Ce script le recalcule à 5 jours depuis la même date de SL fill → expire ~2026-05-25

Usage (bot ARRÊTÉ) :
    cd c:\\Users\\averr\\MULTI_ASSETS
    .venv\\Scripts\\python.exe code/scripts/reset_pepe_cooldown.py

Sécurité :
  - Affiche les valeurs avant/après et demande confirmation avant d'écrire
  - Crée un backup .bak avant modification
"""
import sys
import os
import shutil
from datetime import datetime, timezone

# Ajouter code/src au path pour importer state_manager
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, 'code', 'src'))

from state_manager import load_state, save_state, _get_state_path  # noqa: E402

PAIR = 'PEPEUSDC'
OLD_COOLDOWN_CANDLES = 12
NEW_COOLDOWN_CANDLES = 5
CANDLE_MINUTES_1D = 1440


def ts_to_str(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def main() -> None:
    state_path = _get_state_path()
    print(f"Fichier d'état : {state_path}")

    state = load_state()
    if PAIR not in state:
        print(f"[ERREUR] Paire {PAIR} absente de bot_state. Abandon.")
        sys.exit(1)

    pair_state = state[PAIR]
    current_cooldown_until = pair_state.get('_stop_loss_cooldown_until', 0.0)

    if current_cooldown_until <= 0:
        print(f"[INFO] {PAIR}: _stop_loss_cooldown_until={current_cooldown_until} — pas de cooldown actif. Rien à faire.")
        sys.exit(0)

    old_remaining_min = (current_cooldown_until - datetime.now().timestamp()) / 60
    old_cooldown_min = OLD_COOLDOWN_CANDLES * CANDLE_MINUTES_1D  # 17280 min
    new_cooldown_min = NEW_COOLDOWN_CANDLES * CANDLE_MINUTES_1D  # 7200 min

    # Reconstruire la date de fill SL depuis le cooldown actuel
    sl_fill_ts = current_cooldown_until - (old_cooldown_min * 60)
    new_cooldown_until = sl_fill_ts + (new_cooldown_min * 60)
    new_remaining_min = (new_cooldown_until - datetime.now().timestamp()) / 60

    print(f"\n=== Aperçu des modifications pour {PAIR} ===")
    print(f"  SL fill estimé     : {ts_to_str(sl_fill_ts)}")
    print(f"  Cooldown actuel    : expire {ts_to_str(current_cooldown_until)}  ({old_remaining_min:.0f} min restantes)")
    print(f"  Nouveau cooldown   : expire {ts_to_str(new_cooldown_until)}  ({new_remaining_min:.0f} min restantes)")

    if new_remaining_min < 0:
        print(f"\n[INFO] Nouveau cooldown déjà expiré ({new_remaining_min:.0f} min). Le cooldown sera levé immédiatement.")

    confirm = input("\nAppliquer la modification ? [y/N] ").strip().lower()
    if confirm != 'y':
        print("Abandon — aucun changement effectué.")
        sys.exit(0)

    # Backup avant modification
    bak_path = state_path + '.reset_bak'
    shutil.copy2(state_path, bak_path)
    print(f"Backup créé : {bak_path}")

    pair_state['_stop_loss_cooldown_until'] = new_cooldown_until
    save_state(state)
    print(f"[OK] {PAIR}: cooldown mis à jour → expire {ts_to_str(new_cooldown_until)}")


if __name__ == '__main__':
    main()
