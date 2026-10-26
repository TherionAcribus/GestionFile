"""Auto-détection du relais Socket.IO inter-processus (RabbitMQ).

Le relais n'est plus un réglage applicatif : ``extensions.init_socketio``
branche le ``message_queue`` dès que ``RABBITMQ_URL`` est résolue au
démarrage (variable explicite ou construite depuis RABBITMQ_HOST/USER/
PASSWORD dans config.py). Sans URL : gestionnaire local, diffusion limitée
aux clients du processus — comportement attendu en mono-processus.

``realtime_relay()`` reflète le gestionnaire réellement instancié, pas la
configuration : c'est lui que l'administration affiche.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

import extensions


def _init_kwargs(rabbitmq_url):
    """Appelle init_socketio et retourne les kwargs passés à init_app."""
    app = Flask(__name__)
    app.config["RABBITMQ_URL"] = rabbitmq_url
    with patch.object(extensions.socketio, "init_app") as init_app:
        extensions.init_socketio(app)
    init_app.assert_called_once()
    return init_app.call_args.kwargs


@pytest.mark.parametrize("url", [None, ""])
def test_pas_de_message_queue_sans_url(url):
    """URL absente ou vide : pas de relais — les émissions d'un processus
    sans clients (scheduler) seraient perdues, mais c'est le mode correct
    pour un déploiement mono-processus."""
    assert "message_queue" not in _init_kwargs(url)


def test_message_queue_des_que_url_resolue():
    """Une URL résolue suffit : plus aucun booléen applicatif ne peut
    contredire l'infrastructure (l'ancien switch START_RABBITMQ laissait
    le scheduler émettre dans le vide)."""
    url = "amqp://user:pass@rabbitmq:5672/%2F"
    assert _init_kwargs(url)["message_queue"] == url


class BaseManager:  # noms repris par realtime_relay()
    pass


class KombuManager:
    pass


class PulsarManager:
    pass


def _relay_for(manager):
    fake = SimpleNamespace(server=SimpleNamespace(manager=manager))
    with patch.object(extensions, "socketio", fake):
        return extensions.realtime_relay()


def test_realtime_relay_gestionnaires_connus():
    assert _relay_for(BaseManager()) == "none"
    assert _relay_for(KombuManager()) == "rabbitmq"


def test_realtime_relay_gestionnaire_inconnu_et_absent():
    assert _relay_for(PulsarManager()) == "pulsarmanager"
    with patch.object(extensions, "socketio", SimpleNamespace()):
        assert extensions.realtime_relay() is None
