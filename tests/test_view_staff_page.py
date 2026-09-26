"""Page /admin/staff refaite : cartes lisibles et règles de l'équipe.

Couvre :
- le noyau pur ``staff_explain`` (initiales = code de connexion, unicité sans
  tenir compte de la casse ; compétences ; demandes nominatives non
  appelables ; langue parasite « False ») ;
- les routes : liste en cartes (comptoir occupé, avertissements),
  modification/création par formulaire (erreur = 204 sans remplacement),
  suppression d'un membre connecté et confirmation détaillée.

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import routes.admin_staff as admin_staff
from models import Activity, Counter, Pharmacist, Role, User, db
from staff_explain import (
    COMPETENCES_ALL, COMPETENCES_NONE, COMPETENCES_SOME,
    clean_language, competences, initials_taken, nominative_missing, normalize_initials,
)

SERVEUR_DIR = Path(__file__).resolve().parents[1]


# --- Noyau pur ----------------------------------------------------------------

def test_initiales_sans_tenir_compte_de_la_casse():
    assert normalize_initials("  ma ") == "MA"
    others = [SimpleNamespace(initials="MA"), SimpleNamespace(initials="jd")]
    assert initials_taken("ma", others) and initials_taken("JD", others)
    assert not initials_taken("MB", others)


def test_langue_parasite():
    assert clean_language(False) == "" and clean_language("False") == ""
    assert clean_language(None) == "" and clean_language(" anglais ") == "anglais"


def test_competences():
    acts = [SimpleNamespace(id=1, name="A"), SimpleNamespace(id=2, name="B")]
    assert competences([1, 2], acts) == (COMPETENCES_ALL, ["A", "B"])
    assert competences([2], acts) == (COMPETENCES_SOME, ["B"])
    assert competences([], acts)[0] == COMPETENCES_NONE
    assert competences([1], [])[0] == COMPETENCES_NONE


def test_demande_nominative_non_appelable():
    nomi = [SimpleNamespace(id=9, name="Voir Marie", staff_id=1),
            SimpleNamespace(id=10, name="Voir Jean", staff_id=2)]
    assert nominative_missing(1, [3], nomi) == ["Voir Marie"]
    assert nominative_missing(1, [9], nomi) == []


# --- Routes ---------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    app.register_blueprint(admin_staff.admin_staff_bp)
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    def test_login():
        from flask_login import login_user
        login_user(User.query.filter_by(username="admin").first())
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login", view_func=test_login, methods=["POST"])

    with app.app_context():
        db.create_all()
        admin = User(username="admin", email="a@a.a",
                     password=generate_password_hash("x"), active=True)
        admin.roles.append(Role(name="admin", admin_staff=True))
        db.session.add(admin)
        ordo = Activity(name="Ordonnance", letter="A")
        conseil = Activity(name="Conseil", letter="C")
        marie = Pharmacist(name="Marie", initials="MA", language=False,
                           activities=[ordo, conseil])
        jean = Pharmacist(name="Jean", initials="JD", language="anglais")
        db.session.add_all([ordo, conseil, marie, jean])
        db.session.flush()
        db.session.add_all([
            Activity(name="Voir Marie", letter="M", is_staff=True, staff=marie),
            Counter(name="Comptoir 1", staff=marie, sort_order=1),
        ])
        db.session.commit()
    with patch.object(admin_staff, "communikation", lambda *a, **k: None), \
            patch("ui_feedback.communikation", lambda *a, **k: None):
        yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def _member(app, name):
    with app.app_context():
        m = Pharmacist.query.filter_by(name=name).one()
        return SimpleNamespace(id=m.id, initials=m.initials, language=m.language,
                               activities=sorted(a.name for a in m.activities))


def _act(app, name):
    with app.app_context():
        return Activity.query.filter_by(name=name).one().id


def test_liste_en_cartes(client):
    html = client.get("/admin/staff/table").get_data(as_text=True)
    assert "data-filter-item" in html
    assert "2 membres ·" in html and "1 au comptoir en ce moment" in html
    assert "Au comptoir Comptoir 1" in html and "Hors comptoir" in html
    assert "Peut appeler <strong>tous les patients</strong>" in html  # Marie
    assert "Aucune compétence" in html  # Jean
    # « Voir Marie » désigne Marie mais n'est pas dans ses compétences.
    assert "absent de ses compétences" in html
    assert "Langues : anglais" in html and "Langues : False" not in html
    assert 'name="activities"' in html


def test_modification_initiales_deja_prises_casse_ignoree(app, client):
    jean = _member(app, "Jean")
    response = client.post(f"/admin/staff/member_update/{jean.id}",
                           data={"name": "Jean", "initials": "ma"})
    assert response.status_code == 204
    assert _member(app, "Jean").initials == "JD"


def test_modification_reussie(app, client):
    jean = _member(app, "Jean")
    response = client.post(f"/admin/staff/member_update/{jean.id}",
                           data={"name": "Jean", "initials": " jb ", "language": "anglais",
                                 "activities": [str(_act(app, "Conseil"))]})
    assert response.status_code == 200
    assert "Peut appeler : Conseil" in response.get_data(as_text=True)
    updated = _member(app, "Jean")
    assert updated.initials == "JB" and updated.activities == ["Conseil"]


def test_garder_ses_propres_initiales(app, client):
    marie = _member(app, "Marie")
    response = client.post(f"/admin/staff/member_update/{marie.id}",
                           data={"name": "Marie", "initials": "ma"})
    assert response.status_code == 200
    assert _member(app, "Marie").initials == "MA"


@pytest.mark.parametrize("data", [
    {"name": "", "initials": "XX"},
    {"name": "Paul", "initials": ""},
    {"name": "Paul", "initials": "ABCDEFGHIJK"},
    {"name": "Paul", "initials": "Ma"},
])
def test_creation_invalide(client, data):
    assert client.post("/admin/staff/add_new_staff", data=data).status_code == 204


def test_creation(app, client):
    response = client.post("/admin/staff/add_new_staff",
                           data={"name": "Paul", "initials": "pl",
                                 "activities": [str(_act(app, "Ordonnance"))]})
    assert response.status_code == 200
    assert "div_add_staff_form" in response.get_data(as_text=True)
    assert _member(app, "Paul").initials == "PL"


def test_formulaire_de_creation_toutes_competences_cochees(client):
    html = client.get("/admin/staff/add_form").get_data(as_text=True)
    assert html.count('name="activities"') == 3  # 2 ordinaires + 1 nominative
    assert "data-check-all" in html
    # Les activités ordinaires sont cochées, la demande nominative non.
    nominative = html.split("Voir Marie", 1)[0].rsplit("<input", 1)[1]
    assert "checked" not in nominative


def test_confirmation_suppression_detaillee(app, client):
    marie = _member(app, "Marie")
    html = client.get(f"/admin/staff/confirm_delete/{marie.id}").get_data(as_text=True)
    assert "Comptoir 1" in html and "Voir Marie" in html


def test_suppression_membre_connecte(app, client):
    marie = _member(app, "Marie")
    response = client.delete(f"/admin/staff/delete/{marie.id}")
    assert response.status_code == 200
    with app.app_context():
        assert Pharmacist.query.filter_by(name="Marie").first() is None
        assert Counter.query.filter_by(name="Comptoir 1").one().staff_id is None
        assert Activity.query.filter_by(name="Voir Marie").one().staff_id is None
