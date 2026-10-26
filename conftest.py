"""Configuration pytest commune a toute la suite serveur.

Deux roles, qui evitent chacun une bidouille repetee dans les fichiers de test :

1. **Racine du depot sur ``sys.path``.** Les tests importent les modules du
   serveur a plat (``from audit_log import ...``, ``from pagination import ...``).
   Sans ce fichier, seul ``python -m pytest`` fonctionnait -- car c'est
   l'interpreteur, et non pytest, qui ajoutait le repertoire courant a
   ``sys.path``. Un simple ``pytest`` echouait des la collecte. Douze fichiers de
   test compensaient avec un ``sys.path.insert(...)`` copie-colle en tete ; ce
   conftest le fait une fois pour toutes.

2. **Variables d'environnement de demarrage.** ``app.py`` lit ces deux drapeaux
   au moment de l'import : ``SKIP_EVENTLET_PATCH`` (evite le monkey-patching
   eventlet, incompatible avec l'execution synchrone des tests) et
   ``SKIP_STARTUP_HOOKS`` (evite l'initialisation de la base et des taches de
   demarrage). On utilise ``setdefault`` : une valeur posee explicitement par
   l'appelant reste prioritaire, ce qui permet de lancer volontairement une
   suite d'integration complete.

3. **Serveur reel en opt-in.** Les tests marques ``live_server`` pilotent un
   serveur deja demarre (connexion admin, brouillons, publications,
   inscriptions a la borne). Ils sont ignores tant que ``E2E_BASE_URL`` n'est
   pas definie : un ``pytest -m e2e`` lance alors qu'un serveur de
   developpement tourne ne doit jamais modifier cette installation. Pour les
   lancer : ``E2E_BASE_URL=http://127.0.0.1:5000 pytest -m live_server``
   (de preference contre une base de test).
"""

import os
import sys

_RACINE = os.path.dirname(os.path.abspath(__file__))

if _RACINE not in sys.path:
    sys.path.insert(0, _RACINE)

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")
# Secret factice : sans lui, create_app() refuse de demarrer (point 1.x). Il
# n'ouvre aucun acces -- les tests qui verifient l'authentification posent leur
# propre secret.
os.environ.setdefault("APP_SECRET", "secret-de-test-non-production-ne-pas-reutiliser")

# Relais RabbitMQ : la suite de reference simule un deploiement mono-processus.
# init_socketio cable le message_queue des que RABBITMQ_URL est resolue
# (auto-detection) ; une URL heritee du .env local de la machine produirait un
# KombuManager, que flask_socketio.test_client refuse explicitement. On pose
# des valeurs vides AVANT tout import de config.py : load_dotenv(override=False)
# ne les ecrase pas et l'URL resout alors a None. Un test voulant le relais
# renseigne app.config["RABBITMQ_URL"] lui-meme (cf. tests/test_socketio_relay.py).
for _var in ("RABBITMQ_URL", "RABBITMQ_HOST", "RABBITMQ_USER", "RABBITMQ_PASSWORD"):
    os.environ[_var] = ""


def live_server_url():
    """Adresse du serveur cible des tests ``live_server`` (sans « / »
    final), ou chaîne vide si elle n'a pas été fournie explicitement."""
    return os.environ.get("E2E_BASE_URL", "").strip().rstrip("/")


def pytest_collection_modifyitems(config, items):
    import pytest

    if live_server_url():
        return
    skip = pytest.mark.skip(
        reason="serveur réel : définir E2E_BASE_URL pour lancer ce test "
               "(il modifie l'installation ciblée)")
    for item in items:
        if "live_server" in item.keywords:
            item.add_marker(skip)
