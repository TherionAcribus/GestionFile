"""Régression : l'alerte sonore (beep) ne doit plus tuer l'annonce d'appel.

``communikation("update_audio")`` est invoquée depuis le **thread** du
dispatcher d'annonces (``trigger_async_audio_calling``) — un simple contexte
applicatif, SANS requête active. Le beep utilisait ``url_for(_external=True)``,
qui exige une requête ou ``SERVER_NAME`` : RuntimeError, et l'annonce entière
(beep + MP3) n'était jamais émise — l'écran recevait ``add_calling`` mais
restait muet. Le test admin passait, lui, par un contexte requête, ce qui
masquait le défaut.

Le chemin est désormais relatif (comme ``_public_url`` pour les MP3) : il
fonctionne en requête comme en thread, et évite les soucis de nom d'hôte
derrière un reverse proxy.
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

import communication


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        ANNOUNCE_ALERT=True,
        ANNOUNCE_ALERT_FILENAME="beep.wav",
        ANNOUNCE_PLAYER="web",
    )
    return app


@pytest.fixture
def emissions(monkeypatch):
    """Capture ce qui part réellement vers les clients Socket.IO."""
    captures = []

    def faux_emit(stream, data=None, flag=None, client_id=None,
                  event="update", revision=None, room=None):
        captures.append((stream, event, data))

    monkeypatch.setattr(communication, "communication_websocket", faux_emit)
    return captures


def test_alerte_audio_sans_contexte_requete(application, emissions):
    """Contexte applicatif seul (thread du dispatcher) : les deux évènements
    'audio' partent — beep puis MP3. Avant le fix : RuntimeError, rien n'était
    émis."""
    with application.app_context():
        communication.communikation(
            "update_audio", event="audio",
            data="/static/audio/annonces/x.mp3")

    annonces = [d for stream, event, d in emissions
                if (stream, event) == ("socket_update_screen", "audio")]
    assert len(annonces) == 2
    assert annonces[0] == "/static/audio/signals/beep.wav"
    assert annonces[1] == "/static/audio/annonces/x.mp3"


def test_alerte_audio_chemin_relatif(application, emissions):
    """Le beep est un chemin relatif : jouable par le navigateur sur la même
    origine, sans dépendre du nom d'hôte vu par le serveur."""
    with application.app_context():
        communication.communikation("update_audio", event="audio", data="x.mp3")

    beep = next(d for _s, _e, d in emissions if "signals" in str(d))
    assert beep == "/static/audio/signals/beep.wav"
    assert not beep.startswith("http")


def test_alerte_desactivee_n_emet_que_le_mp3(application, emissions):
    application.config["ANNOUNCE_ALERT"] = False
    with application.app_context():
        communication.communikation("update_audio", event="audio", data="x.mp3")

    annonces = [d for stream, event, d in emissions
                if (stream, event) == ("socket_update_screen", "audio")]
    assert annonces == ["x.mp3"]
