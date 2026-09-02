# Ce test importe app.py en entier : il ouvre une connexion MySQL et execute
# les hooks de demarrage. Marque `mysql` -> exclu de la suite par defaut
# (voir pytest.ini). Lancer avec : pytest -m mysql
import pytest

pytestmark = pytest.mark.mysql

from app import app
import unittest

class BasicTests(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True

    def test_home(self):
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Bonjour le monde!', response.data)