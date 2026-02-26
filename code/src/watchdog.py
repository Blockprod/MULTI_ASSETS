import sys
import os
# Ajout du dossier bin/ au sys.path pour les modules Cython
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'bin')))

import time
import subprocess
import logging
from datetime import datetime

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

class TradingBotWatchdog:
    def __init__(self, script_path="MULTI_SYMBOLS.py", check_interval=60):
        self.script_path = script_path
        self.check_interval = check_interval
        self.process = None
        self.restart_count = 0
        self.max_restarts_per_hour = 5
        self.restart_times = []
        
    def is_process_running(self):
        """Vérifie si le processus du bot est en cours d'exécution"""
        if self.process is None:
            return False
        return self.process.poll() is None
    
    def start_bot(self):
        """Démarre le bot de trading"""
        try:
            logger.info("Démarrage du bot de trading...")
            self.process = subprocess.Popen([
                sys.executable, self.script_path
            ], cwd=os.path.dirname(os.path.abspath(self.script_path)))
            logger.info(f"Bot démarré avec PID: {self.process.pid}")
            return True
        except Exception as e:
            logger.error(f"Erreur lors du démarrage du bot: {e}")
            return False
    
    def stop_bot(self):
        """Arrête le bot de trading"""
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
        """Vérifie si on peut redémarrer (limite de redémarrages)"""
        now = datetime.now()
        # Nettoyer les redémarrages de plus d'1 heure
        self.restart_times = [t for t in self.restart_times 
                             if (now - t).seconds < 3600]
        
        if len(self.restart_times) >= self.max_restarts_per_hour:
            logger.error("Trop de redémarrages en 1 heure. Arrêt du watchdog.")
            return False
        return True
    
    def restart_bot(self):
        """Redémarre le bot"""
        if not self.should_restart():
            return False
            
        logger.warning("Redémarrage du bot...")
        self.stop_bot()
        time.sleep(5)  # Attendre avant redémarrage
        
        if self.start_bot():
            self.restart_count += 1
            self.restart_times.append(datetime.now())
            logger.info(f"Bot redémarré (#{self.restart_count})")
            return True
        return False
    
    def run(self):
        """Boucle principale du watchdog"""
        logger.info("Watchdog démarré")
        
        # Démarrage initial
        if not self.start_bot():
            logger.error("Impossible de démarrer le bot initialement")
            return
        
        try:
            while True:
                time.sleep(self.check_interval)
                
                if not self.is_process_running():
                    logger.warning("Bot arrêté détecté")
                    if not self.restart_bot():
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