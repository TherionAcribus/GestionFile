"""Profil de préférences par équipier — routes/pyside_preferences.py.

Application Flask minimale + SQLite en mémoire (patron de
``test_account_recovery.py``) :

1. garde d'accès : jeton applicatif exigé (``require_app_token``) ;
2. GET : 404 équipier inconnu, 404 sans profil, 200 + payload avec profil ;
3. PUT : validation du corps, création puis écrasement (upsert),
   version de schéma, horodatage, plafond de taille.
"""

import os

import pytest
from flask import Flask

from auth_utils import generate_app_token
from models import db, Pharmacist, StaffPreference
from routes.pyside_preferences import staff_prefs_bp


def _make_app():
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        TESTING=True,
        SECRET_KEY="test-staff-prefs",
    )
    db.init_app(app)
    app.register_blueprint(staff_prefs_bp, url_prefix="")
    with app.app_context():
        db.create_all()
        jj = Pharmacist(name="Jeanne J.", initials="JJ", is_active=True)
        db.session.add(jj)
        db.session.commit()
    return app


@pytest.fixture
def app():
    app = _make_app()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def staff_id(app):
    with app.app_context():
        return Pharmacist.query.filter_by(initials="JJ").first().id


def _auth_headers(app):
    with app.app_context():
        token = generate_app_token()
    return {"X-App-Token": token}


# ---------------------------------------------------------------------------
# Garde d'accès
# ---------------------------------------------------------------------------

def test_get_sans_jeton_refuse(client, staff_id):
    assert client.get(
        f"/api/pyside/staff_preferences/{staff_id}").status_code == 401


def test_put_sans_jeton_refuse(client, staff_id):
    assert client.put(
        f"/api/pyside/staff_preferences/{staff_id}",
        json={"payload": {"a": 1}}).status_code == 401


def test_jeton_invalide_refuse(client, staff_id):
    assert client.get(
        f"/api/pyside/staff_preferences/{staff_id}",
        headers={"X-App-Token": "bidon"}).status_code == 401


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------

def test_get_equipier_inconnu(app, client):
    assert client.get(
        "/api/pyside/staff_preferences/99999",
        headers=_auth_headers(app)).status_code == 404


def test_get_sans_profil_404(app, client, staff_id):
    resp = client.get(
        f"/api/pyside/staff_preferences/{staff_id}",
        headers=_auth_headers(app))
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "no_preferences"


# ---------------------------------------------------------------------------
# PUT puis GET
# ---------------------------------------------------------------------------

def test_put_cree_puis_get(app, client, staff_id):
    headers = _auth_headers(app)
    payload = {"shortcut_next": "Alt+S", "volume": 70}
    resp = client.put(
        f"/api/pyside/staff_preferences/{staff_id}",
        json={"payload": payload, "schema_version": 1},
        headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["staff_id"] == staff_id
    assert data["payload"] == payload
    assert data["schema_version"] == 1
    assert data["updated_at"]

    resp = client.get(
        f"/api/pyside/staff_preferences/{staff_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["payload"] == payload


def test_put_ecrase_le_profil(app, client, staff_id):
    headers = _auth_headers(app)
    url = f"/api/pyside/staff_preferences/{staff_id}"
    client.put(url, json={"payload": {"v": 1}}, headers=headers)
    resp = client.put(url, json={"payload": {"v": 2}}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["payload"] == {"v": 2}
    # Une seule ligne par équipier.
    with app.app_context():
        assert StaffPreference.query.filter_by(
            staff_id=staff_id).count() == 1


def test_put_equipier_inconnu_404(app, client):
    assert client.put(
        "/api/pyside/staff_preferences/99999",
        json={"payload": {"a": 1}},
        headers=_auth_headers(app)).status_code == 404


def test_put_payload_non_objet_400(app, client, staff_id):
    headers = _auth_headers(app)
    url = f"/api/pyside/staff_preferences/{staff_id}"
    for bad in ([1, 2], "texte", 42, None):
        assert client.put(
            url, json={"payload": bad}, headers=headers).status_code == 400


def test_put_payload_trop_gros_413(app, client, staff_id):
    gros = {"bloc": "x" * (70 * 1024)}
    resp = client.put(
        f"/api/pyside/staff_preferences/{staff_id}",
        json={"payload": gros},
        headers=_auth_headers(app))
    assert resp.status_code == 413


def test_put_schema_version_normalisee(app, client, staff_id):
    resp = client.put(
        f"/api/pyside/staff_preferences/{staff_id}",
        json={"payload": {}, "schema_version": "abc"},
        headers=_auth_headers(app))
    assert resp.status_code == 200
    assert resp.get_json()["schema_version"] == 1
