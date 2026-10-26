"""Cookies de suivi du téléphone patient.

Avant : posés sans HttpOnly ni SameSite (ni Secure en HTTPS), avec un
commentaire annonçant 20 min pour une durée réelle de 30 min.

Verrouillé ici : HttpOnly sauf ``patient_call_number`` (lu par phone.js),
SameSite=Lax, Secure quand COOKIE_SECURE est actif, 30 minutes, et le même
traitement à l'effacement.
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

from types import SimpleNamespace

import pytest
from flask import Flask, make_response

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _set_cookie_headers(secure, action):
    import routes.patient as patient_routes
    app = Flask(__name__)
    app.config.update(SECRET_KEY="secret-test-phone-cookies",
                      SESSION_COOKIE_SECURE=secure)
    with app.test_request_context("/patient/phone/ping"):
        response = make_response("")
        if action == "set":
            patient_routes._set_phone_cookies(
                response, SimpleNamespace(id=12, call_number="A-7"))
        else:
            patient_routes._clear_phone_cookies(response)
    headers = response.headers.getlist("Set-Cookie")
    return {h.split("=", 1)[0]: h for h in headers}


@pytest.mark.parametrize("action", ["set", "clear"])
def test_attributs_des_cookies(action):
    cookies = _set_cookie_headers(secure=False, action=action)
    assert set(cookies) == {"patient_id", "patient_call_number", "patient_token"}
    for name, header in cookies.items():
        assert "SameSite=Lax" in header, name
        assert "Secure" not in header, name
    assert "HttpOnly" in cookies["patient_id"]
    assert "HttpOnly" in cookies["patient_token"]
    # phone.js lit le numéro d'appel pour rejoindre sa salle Socket.IO.
    assert "HttpOnly" not in cookies["patient_call_number"]


def test_duree_trente_minutes():
    cookies = _set_cookie_headers(secure=False, action="set")
    for name, header in cookies.items():
        assert "Max-Age=1800" in header, name
    assert "A-7" in cookies["patient_call_number"]


def test_secure_en_https():
    for action in ("set", "clear"):
        cookies = _set_cookie_headers(secure=True, action=action)
        for name, header in cookies.items():
            assert "Secure" in header, (action, name)


def test_plus_aucun_set_cookie_brut_dans_les_routes_patient():
    with open(os.path.join(_SERVEUR, "routes", "patient.py"), encoding="utf-8") as fh:
        source = fh.read()
    # Tous les cookies téléphone passent par les deux helpers.
    assert source.count("response.set_cookie(") == 2
    assert "20 minutes" not in source


def test_phone_js_ne_lit_que_le_numero_d_appel():
    with open(os.path.join(_SERVEUR, "static", "js", "phone.js"), encoding="utf-8") as fh:
        source = fh.read()
    assert "patient_call_number=" in source
    assert "patient_id=" not in source and "patient_token=" not in source
