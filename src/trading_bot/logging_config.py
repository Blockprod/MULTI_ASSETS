"""
Module de configuration centralisée du logging pour le bot de trading.
"""

import logging

LOG_FILE = "trading_bot.log"
LOG_LEVEL = logging.INFO
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Configuration du logger racine
logging.basicConfig(
    level=LOG_LEVEL,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


def get_logger(name: str = __name__):
    """Retourne un logger configuré pour le module donné."""
    return logging.getLogger(name)
