"""
WATCHDOG MODULE — Process monitor + heartbeat consumer for Trading Bot.

Two-layer health detection:
  1. Process-level: is the PID still alive?
  2. Heartbeat-level: is the bot still looping? (heartbeat.json freshness)

If the heartbeat goes stale (> HEARTBEAT_STALE_SECONDS), the watchdog
considers the bot hung and restarts it, even if the OS process is alive.
"""

import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

import json
import time
import subprocess
import logging
from datetime import datetime, timezone

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - WATCHDOG - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('watchdog.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Heartbeat staleness threshold (seconds). If heartbeat.json is older than
# this, the bot is considered hung even if the process is still running.
HEARTBEAT_STALE_SECONDS = 600  # 10 minutes (main loop sleeps 120s)


class TradingBotWatchdog:
    def __init__(self, script_path="MULTI_SYMBOLS.py", check_interval=60,
                 heartbeat_path=None):
        self.script_path = script_path
        self.check_interval = check_interval
        self.process = None
        self.restart_count = 0
        self.max_restarts_per_hour = 5
        self.restart_times = []
        # Default heartbeat path: states/heartbeat.json relative to script dir
        if heartbeat_path is None:
            script_dir = os.path.dirname(os.path.abspath(self.script_path))
            self.heartbeat_path = os.path.join(script_dir, "states", "heartbeat.json")
        else:
            self.heartbeat_path = heartbeat_path

    def is_process_running(self):
        """Vérifie si le processus du bot est en cours d'exécution."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def is_heartbeat_fresh(self) -> bool:
        """Check if the heartbeat file exists and is recent enough.

        Returns True if:
          - The file exists AND was written less than HEARTBEAT_STALE_SECONDS ago.
          - OR the file does not exist (benefit of the doubt during startup).
        Returns False if the file exists but is stale.
        """
        if not os.path.exists(self.heartbeat_path):
            return True
        try:
            with open(self.heartbeat_path, "r") as f:
                data = json.load(f)
            ts_str = data.get("timestamp", "")
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > HEARTBEAT_STALE_SECONDS:
                logger.warning(f"Heartbeat stale: {age:.0f}s old (limit {HEARTBEAT_STALE_SECONDS}s)")
                return False
            return True
        except Exception as e:
            logger.error(f"Error reading heartbeat: {e}")
            return True  # Don't restart on read errors

    def start_bot(self):
        """Démarre le bot de trading."""
        try:
            logger.info("Démarrage du bot de trading...")
            self.process = subprocess.Popen(
                [sys.executable, self.script_path],
                cwd=os.path.dirname(os.path.abspath(self.script_path))
            )
            logger.info(f"Bot démarré avec PID: {self.process.pid}")
            return True
        except Exception as e:
            logger.error(f"Erreur lors du démarrage du bot: {e}")
            return False

    def stop_bot(self):
        """Arrête le bot de trading."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=30)
                logger.info("Bot arrêté proprement")
            except subprocess.TimeoutExpired:
                self.process.kill()
                logger.warning("Bot forcé à s'arrêter")
            except Exception as e:
                logger.error(f"Erreur lors de l'arrêt: {e}")

    def should_restart(self):
        """Vérifie si on peut redémarrer (limite de redémarrages)."""
        now = datetime.now()
        self.restart_times = [t for t in self.restart_times
                              if (now - t).seconds < 3600]
        if len(self.restart_times) >= self.max_restarts_per_hour:
            logger.error("Trop de redémarrages en 1 heure. Arrêt du watchdog.")
            return False
        return True

    def restart_bot(self, reason: str = "unknown"):
        """Redémarre le bot."""
        if not self.should_restart():
            return False

        logger.warning(f"Redémarrage du bot (raison: {reason})...")
        self.stop_bot()
        time.sleep(5)

        if self.start_bot():
            self.restart_count += 1
            self.restart_times.append(datetime.now())
            logger.info(f"Bot redémarré (#{self.restart_count})")
            return True
        return False

    def run(self):
        """Boucle principale du watchdog."""
        logger.info("Watchdog démarré")

        if not self.start_bot():
            logger.error("Impossible de démarrer le bot initialement")
            return

        try:
            while True:
                time.sleep(self.check_interval)

                if not self.is_process_running():
                    logger.warning("Bot arrêté détecté (process dead)")
                    if not self.restart_bot(reason="process_dead"):
                        logger.error("Impossible de redémarrer. Arrêt du watchdog.")
                        break
                elif not self.is_heartbeat_fresh():
                    logger.warning("Bot bloqué détecté (heartbeat stale)")
                    if not self.restart_bot(reason="heartbeat_stale"):
                        logger.error("Impossible de redémarrer. Arrêt du watchdog.")
                        break
                else:
                    logger.debug("Bot fonctionne normalement")

        except KeyboardInterrupt:
            logger.info("Arrêt du watchdog demandé")
            self.stop_bot()
        except Exception as e:
            logger.error(f"Erreur watchdog: {e}")
            self.stop_bot()


if __name__ == "__main__":
    watchdog = TradingBotWatchdog()
    watchdog.run()