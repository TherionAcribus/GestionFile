"""Parcours de configuration guidée (« onboarding » admin).

Couvre, sur application Flask isolée + SQLite :

- le catalogue et les transitions pures de ``onboarding.py`` ;
- la persistance ``AdminOnboardingState`` (création sur action explicite
  uniquement — jamais en GET) ;
- les routes : actions en liste fermée, verrouillage optimiste (version -> 409),
  refus anonyme, filtrage par permissions, indépendance entre comptes ;
- les fragments HTMX (panneau, checklist, accueil) et les context processors.

Pas de SQLALCHEMY_BINDS : le bind resterait dans les métadonnées de l'extension
``db`` partagée entre fichiers de test (cf. tests/test_admin_bootstrap.py).
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import onboarding as ob
import routes.admin_onboarding as admin_onboarding
from models import AdminOnboardingState, Role, User, db

SERVEUR_DIR = Path(__file__).resolve().parents[1]

_ADMIN_PERMS = dict(
    admin_security=True, admin_app=True, admin_activity=True, admin_staff=True,
    admin_counter=True, admin_patient=True, admin_announce=True,
)


@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, root_path=str(SERVEUR_DIR))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    # Gabarits : base.html utilise csrf_token() et user_has_permission, fournis
    # dans l'app réelle par CSRFProtect et inject_user — stubbés ici.
    app.jinja_env.globals["csrf_token"] = lambda: "test-token"
    app.context_processor(
        lambda: {"user_has_permission": __import__(
            "routes.admin_security", fromlist=["x"]).user_has_permission})

    app.register_blueprint(admin_onboarding.admin_onboarding_bp)
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    with app.app_context():
        db.create_all()
        full = Role(name="admin", **_ADMIN_PERMS)
        partial = Role(name="borne", admin_patient=True)
        db.session.add_all([full, partial])
        alice = User(username="alice", email="a@a.a",
                     password=generate_password_hash("x"), active=True)
        alice.roles.append(full)
        bob = User(username="bob", email="b@b.b",
                   password=generate_password_hash("x"), active=True)
        bob.roles.append(partial)
        db.session.add_all([alice, bob])
        db.session.commit()

    with patch("ui_feedback.communikation", lambda *a, **k: None):
        yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _login(app, client, username):
    """Pose la session flask_login directement (fs_uniquifier)."""
    with app.app_context():
        uniquifier = User.query.filter_by(username=username).one().fs_uniquifier
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
    return client


@pytest.fixture()
def client(app):
    return _login(app, app.test_client(), "alice")


def _state(app, username="alice"):
    with app.app_context():
        user = User.query.filter_by(username=username).one()
        return AdminOnboardingState.query.filter_by(user_id=user.id).first()


def _post(client, headers=None, **fields):
    return client.post("/admin/onboarding/action", data=fields,
                       headers=headers or {})


def _post_json(client, payload):
    return client.post("/admin/onboarding/action", json=payload)


# --- Noyau pur ----------------------------------------------------------------

def test_catalogue_8_etapes():
    ids = [s["id"] for s in ob.catalog()]
    assert ids == ["security", "pharmacy", "activities", "staff", "counters",
                   "patient", "announce", "summary"]
    assert ob.step_by_id("security")["url"] == "/admin/security"
    assert ob.step_by_id("summary")["permission"] is None
    assert ob.step_by_id("inconnue") is None


def test_step_for_path():
    assert ob.step_for_path("/admin/security") == "security"
    assert ob.step_for_path("/admin/app/general") == "pharmacy"
    assert ob.step_for_path("/admin/announce/audio") == "announce"
    assert ob.step_for_path("/admin/onboarding") is None
    assert ob.step_for_path("/admin") is None
    assert ob.step_for_path("/admin/appschedule") is None  # préfixe strict


def test_filtrage_par_permissions():
    can_all = lambda perm: True
    can_patient = lambda perm: perm == "patient"
    assert [s["id"] for s in ob.available_steps(can_patient)] == ["patient", "summary"]
    assert ob.first_available_step_id(can_patient) == "patient"
    assert ob.first_available_step_id(can_all) == "security"
    prog = ob.progress({}, can_patient)
    assert prog["total"] == 1 and prog["unavailable"] == 6


def test_transitions_pures():
    can = lambda perm: True
    # verify sur l'étape courante fait avancer le curseur.
    state, err = ob.apply_action(None, "start", None, can)
    assert err is None and state["status"] == "active" and state["current_step"] == "security"
    state, err = ob.apply_action(state, "verify", "security", can)
    assert ob.step_status(state["steps"], "security") == "verified"
    assert state["current_step"] == "pharmacy"
    # pause bloque les actions d'étape ; resume les débloque.
    paused, err = ob.apply_action(state, "pause", None, can)
    assert err is None
    assert ob.apply_action(paused, "verify", "pharmacy", can)[1] == "etat_invalide"
    resumed, err = ob.apply_action(paused, "resume", None, can)
    assert err is None and resumed["status"] == "active"
    # skip ne compte pas comme vérifié.
    skipped, _ = ob.apply_action(resumed, "skip", "pharmacy", can)
    prog = ob.progress(skipped["steps"], can)
    assert prog["verified"] == 1 and prog["skipped"] == 1
    # finish clôt le parcours.
    done, err = ob.apply_action(skipped, "finish", None, can)
    assert err is None and done["status"] == "completed"
    # Après clôture, les actions d'étape sont refusées.
    assert ob.apply_action(done, "verify", "staff", can)[1] == "etat_invalide"


def test_apply_action_validation():
    can = lambda perm: True
    assert ob.apply_action(None, "teleport", None, can)[1] == "action_inconnue"
    active, _ = ob.apply_action(None, "start", None, can)
    assert ob.apply_action(active, "verify", "nope", can)[1] == "etape_inconnue"
    assert ob.apply_action(active, "verify", "summary", can)[1] == "etape_inconnue"
    # Étape hors des droits de l'utilisateur.
    limited = lambda perm: perm == "patient"
    partial, _ = ob.apply_action(None, "start", None, limited)
    assert ob.apply_action(partial, "verify", "security", limited)[1] == "etape_interdite"


# --- Routes --------------------------------------------------------------------

def test_get_ne_cree_pas_d_etat(app, client):
    response = client.get("/admin/onboarding")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Configuration guidée" in html and "Sécurité et comptes" in html
    with app.app_context():
        assert AdminOnboardingState.query.count() == 0


def test_anonyme_refuse(app):
    c = app.test_client()
    response = c.get("/admin/onboarding")
    assert response.status_code == 302 and "/login" in response.headers["Location"]
    assert _post(c, action="start").status_code == 401


def test_start_cree_l_etat(app, client):
    response = _post_json(client, {"action": "start"})
    assert response.status_code == 200
    assert response.get_json()["next_url"] == "/admin/onboarding"
    state = _state(app)
    assert state.status == "active" and state.current_step == "security"
    assert state.version == 1


def test_start_htmx_redirige_vers_la_checklist(client):
    response = _post(client, action="start", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/admin/onboarding"


def test_action_inconnue_refusee(app, client):
    assert _post(client, action="hack").status_code == 400
    with app.app_context():
        assert AdminOnboardingState.query.count() == 0


def test_actions_etapes_et_avancee(app, client):
    _post(client, action="start")
    # Étape inconnue -> 400.
    assert _post(client, action="verify", step_id="nope").status_code == 400
    # verify avance le curseur.
    response = _post_json(client, {"action": "verify", "step_id": "security"})
    assert response.status_code == 200
    state = _state(app)
    assert state.steps_state["security"]["status"] == "verified"
    assert state.current_step == "pharmacy" and state.version == 2
    # skip ne compte pas comme vérifié.
    _post_json(client, {"action": "skip", "step_id": "pharmacy"})
    state = _state(app)
    assert state.steps_state["pharmacy"]["status"] == "skipped"
    assert state.current_step == "activities"


def test_select_change_etape_courante(app, client):
    _post(client, action="start")
    _post_json(client, {"action": "select", "step_id": "counters"})
    assert _state(app).current_step == "counters"


def test_pause_resume(app, client):
    _post(client, action="start")
    _post(client, action="pause")
    assert _state(app).status == "paused"
    # Pas d'action d'étape en pause.
    assert _post(client, action="verify", step_id="security").status_code == 400
    _post(client, action="resume")
    assert _state(app).status == "active"


def test_dismiss_et_finish(app, client):
    _post(client, action="dismiss")
    assert _state(app).status == "dismissed"
    # Relance toujours possible après refus.
    _post(client, action="start")
    _post(client, action="finish")
    assert _state(app).status == "completed"


def test_conflit_de_version(app, client):
    _post(client, action="start")  # version 1
    # Écriture basée sur une version périmée -> 409, sans mutation.
    response = _post_json(client, {"action": "verify", "step_id": "security", "version": 99})
    assert response.status_code == 409
    state = _state(app)
    assert state.steps_state == {} and state.version == 1
    # Bonne version -> acceptée ; la version précédente devient obsolète.
    assert _post_json(client, {"action": "verify", "step_id": "security", "version": 1}).status_code == 200
    assert _post_json(client, {"action": "verify", "step_id": "pharmacy", "version": 1}).status_code == 409


def test_deux_comptes_independants(app, client):
    _post(client, action="start")
    bob = _login(app, app.test_client(), "bob")
    _post_json(bob, {"action": "start"})
    _post_json(bob, {"action": "verify", "step_id": "patient"})
    alice_state, bob_state = _state(app, "alice"), _state(app, "bob")
    assert alice_state.id != bob_state.id
    assert alice_state.current_step == "security" and alice_state.steps_state == {}
    assert bob_state.current_step == "summary"
    assert bob_state.steps_state["patient"]["status"] == "verified"


def test_filtrage_permissions_en_route(app, client):
    bob = _login(app, app.test_client(), "bob")  # permission 'patient' seule
    _post_json(bob, {"action": "start"})
    assert _state(app, "bob").current_step == "patient"
    # verify sur une étape hors droits -> 400.
    assert _post_json(bob, {"action": "verify", "step_id": "security"}).status_code == 400
    # La checklist signale les étapes indisponibles et ne rend pas de lien
    # vers une étape hors droits (on se limite au slot de checklist : la
    # sidebar et le pied de page du gabarit gardent leurs propres liens).
    html = bob.get("/admin/onboarding").get_data(as_text=True)
    assert "Non accessible avec vos droits" in html
    slot = html.split('id="onboarding-checklist-slot"', 1)[1]
    slot = slot.split("</ol>", 1)[0]
    assert 'href="/admin/patient/buttons"' in slot
    assert 'href="/admin/security"' not in slot


def test_rendu_checklist_et_bilan(app, client):
    _post(client, action="start")
    html = client.get("/admin/onboarding").get_data(as_text=True)
    assert "onboarding-checklist-slot" in html
    assert "Vérifié par vous" not in html and "À faire" in html
    _post_json(client, {"action": "verify", "step_id": "security"})
    html = client.get("/admin/onboarding").get_data(as_text=True)
    assert "Vérifié par vous" in html


def test_fragments_htmx(app, client):
    htmx = {"HX-Request": "true"}
    _post(client, action="start")
    # verify depuis l'encart (origin=panel) -> navigation vers l'étape suivante.
    response = client.post("/admin/onboarding/action", headers=htmx,
                           data={"action": "verify", "step_id": "security",
                                 "origin": "panel", "version": "1"})
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/admin/app/general"
    # pause depuis l'encart -> fragment panneau « en pause ».
    response = client.post("/admin/onboarding/action", headers=htmx,
                           data={"action": "pause", "step_id": "pharmacy",
                                 "origin": "panel"})
    body = response.get_data(as_text=True)
    assert "onboarding-panel-slot" in body and "en pause" in body
    # resume depuis l'encart -> fragment re-rendu avec la carte de l'étape.
    response = client.post("/admin/onboarding/action", headers=htmx,
                           data={"action": "resume", "step_id": "pharmacy",
                                 "origin": "panel"})
    body = response.get_data(as_text=True)
    assert "Étape guidée" in body
    # skip depuis la checklist -> fragment checklist re-rendu.
    response = client.post("/admin/onboarding/action", headers=htmx,
                           data={"action": "skip", "step_id": "pharmacy",
                                 "origin": "checklist"})
    body = response.get_data(as_text=True)
    assert "onboarding-checklist-slot" in body and "Passé" in body


def test_encart_accueil(client):
    htmx = {"HX-Request": "true"}
    # Sans parcours : l'encart d'accueil propose l'invitation.
    response = client.post("/admin/onboarding/action", headers=htmx,
                           data={"action": "dismiss", "origin": "home"})
    body = response.get_data(as_text=True)
    assert "onboarding-home-slot" in body
    # Après refus, plus d'invitation (ni « Pas maintenant »).
    assert "Pas maintenant" not in body and "Commencer la configuration" not in body


def test_contexte_panneau_selon_page(app, client):
    _post(client, action="start")  # active, étape courante = security
    with app.test_request_context("/admin/security"):
        from flask_login import login_user
        login_user(User.query.filter_by(username="alice").one())
        panel = admin_onboarding.onboarding_panel_context()
        assert panel["step"]["id"] == "security" and panel["status"] == "active"
        assert panel["is_current"] is True
    with app.test_request_context("/admin"):
        assert admin_onboarding.onboarding_panel_context() is None
    with app.test_request_context("/admin/onboarding"):
        assert admin_onboarding.onboarding_panel_context() is None


def test_get_rend_sans_csrf_ni_mutation_metier(app, client):
    """Le GET de la page n'écrit rien, même après un parcours actif."""
    _post(client, action="start")
    response = client.get("/admin/onboarding")
    assert response.status_code == 200
    state = _state(app)
    assert state.status == "active" and state.version == 1


def test_suppression_compte_supprime_progression(app, client):
    """Cascade delete-orphan : la ligne de parcours suit le compte.

    Sans elle, la FK ``user_id`` (nullable=False) bloquait la suppression
    d'un utilisateur ayant un parcours (admin_security.delete_user2)."""
    _post(client, action="start")
    with app.app_context():
        user = User.query.filter_by(username="alice").one()
        db.session.delete(user)
        db.session.commit()
        assert AdminOnboardingState.query.count() == 0
        assert User.query.filter_by(username="alice").first() is None
