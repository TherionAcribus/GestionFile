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
