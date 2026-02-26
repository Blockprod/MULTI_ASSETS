import sys, os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))
import unittest
from trading_bot.config import Config


class TestConfig(unittest.TestCase):
    def test_config_from_env(self):
        # On simule des variables d'environnement minimales
        import os

        os.environ["BINANCE_API_KEY"] = "testkey"
        os.environ["BINANCE_SECRET_KEY"] = "testsecret"
        os.environ["SENDER_EMAIL"] = "test@domain.com"
        os.environ["RECEIVER_EMAIL"] = "dest@domain.com"
        os.environ["GOOGLE_MAIL_PASSWORD"] = "pass"
        config = Config.from_env()
        self.assertEqual(config.api_key, "testkey")
        self.assertEqual(config.secret_key, "testsecret")
        self.assertEqual(config.sender_email, "test@domain.com")
        self.assertEqual(config.receiver_email, "dest@domain.com")
        self.assertEqual(config.smtp_password, "pass")


if __name__ == "__main__":
    unittest.main()
