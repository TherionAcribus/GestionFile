"""Point 1.5 - Source de verite unique des permissions.

Trois familles de verifications, executables sans MySQL ni serveur :

1. Coherence du modele de permissions : le registre
   (permissions_registry.PERMISSIONS), les colonnes admin_* du modele Role et
   les gardes require_permission('<resource>') disseminees dans le code doivent
   tous s'accorder. En particulier, les permissions fantomes admin_music /
   admin_dashboard ne reapparaissent pas et les permissions reelles (music_play,
   music_options, staff, phone, announce, patient, gallery...) sont exposees.

2. Detecteur de garde inconnue : toute route /admin (blueprints routes/admin_*
   et app.py) qui pose un require_permission* doit viser une ressource connue du
   registre. Une nouvelle route gardee par une permission inexistante fait
   echouer ce test.

3. Matrice d'acceptation par domaine, sur une vraie app Flask, en reutilisant
   les decorateurs de production. Pour chaque permission du registre : anonyme
   -> refus ; connecte sans role -> 403 ; connecte avec le mauvais role -> 403
   (y compris sur une sous-route, pas de contournement) ; connecte avec la bonne
   permission -> succes.
"""

import os
import re

import pytest
from flask import Blueprint, Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user

from permissions_registry import (
    PERMISSIONS,
    PERMISSION_FIELDS,
    PERMISSION_RESOURCES,
    KNOWN_RESOURCES,
)
from routes.admin_security import require_permission, require_permission_api


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_ROUTES_DIR = os.path.join(_SERVEUR, "routes")


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Coherence : registre <-> modele <-> gabarits
# ---------------------------------------------------------------------------

def test_registry_matches_model_columns_exactly():
    """Le registre decrit EXACTEMENT les colonnes admin_* du modele Role."""
    from models import Role  # import local : evite d'exiger MySQL au chargement
    model_fields = {c.name for c in Role.__table__.columns
                    if c.name.startswith("admin_")}
    assert model_fields == set(PERMISSION_FIELDS), (
        "Le registre de permissions a diverge du modele Role.\n"
        f"  seulement dans le modele : {sorted(model_fields - set(PERMISSION_FIELDS))}\n"
        f"  seulement dans le registre : {sorted(set(PERMISSION_FIELDS) - model_fields)}"
    )


def test_registry_has_no_duplicates():
    assert len(PERMISSION_RESOURCES) == len(set(PERMISSION_RESOURCES))
    assert len(PERMISSION_FIELDS) == len(set(PERMISSION_FIELDS))


def test_phantom_permissions_are_gone():
    """Les permissions inexistantes du modele ne reviennent pas dans le registre."""
    assert "music" not in KNOWN_RESOURCES
    assert "dashboard" not in KNOWN_RESOURCES
    assert "admin_music" not in PERMISSION_FIELDS
    assert "admin_dashboard" not in PERMISSION_FIELDS


def test_real_permissions_are_exposed():
    """Les domaines jadis absents du formulaire sont bien presents."""
    for resource in ("music_play", "music_options", "staff", "phone",
                     "announce", "patient", "gallery"):
        assert resource in KNOWN_RESOURCES, f"{resource} devrait etre expose"


def test_every_permission_is_fully_described():
    """Chaque entree porte libelle, description, categorie et risque valides."""
    valid_risks = {"high", "medium", "low"}
    for perm in PERMISSIONS:
        assert perm.label.strip(), f"{perm.resource}: libelle manquant"
        assert perm.description.strip(), f"{perm.resource}: description manquante"
        assert perm.category.strip(), f"{perm.resource}: categorie manquante"
        assert perm.risk in valid_risks, f"{perm.resource}: risque invalide"
        assert perm.field == f"admin_{perm.resource}"


def test_add_role_form_no_longer_hardcodes_permissions():
    """Le gabarit d'ajout genere les cases depuis le registre (plus de liste en dur)."""
    tpl = _read("templates/admin/security_add_role_form.html")
    assert 'data-permission="admin_music"' not in tpl
    assert 'data-permission="admin_dashboard"' not in tpl
    assert "permissions_by_category" in tpl
    assert "perm.field" in tpl


# ---------------------------------------------------------------------------
# 2. Detecteur de garde inconnue : toute garde vise une ressource du registre
# ---------------------------------------------------------------------------

_GUARD_CALL_RE = re.compile(
    r"require_permission(?:_api|_dashboard)?\(\s*['\"]([a-z_]+)['\"]")


def _guarded_resources_in(rel):
    """Ressources visees par un require_permission* dans le fichier rel."""
    return set(_GUARD_CALL_RE.findall(_read(rel)))


def _guard_bearing_sources():
    """Sources de production susceptibles de poser des gardes /admin."""
    yield "app.py"
    for name in os.listdir(_ROUTES_DIR):
        if name.startswith("admin_") and name.endswith(".py"):
            yield os.path.join("routes", name)


def test_all_guards_reference_known_permissions():
    """Une route /admin gardee par une permission inconnue fait echouer ce test."""
    unknown = {}
    for rel in _guard_bearing_sources():
        for resource in _guarded_resources_in(rel):
            if resource not in KNOWN_RESOURCES:
                unknown.setdefault(rel, set()).add(resource)

    assert not unknown, (
        "Gardes de permission visant une ressource absente du registre "
        "(permissions_registry.py) :\n  "
        + "\n  ".join(f"{rel}: {sorted(res)}" for rel, res in sorted(unknown.items()))
    )


def test_registry_permissions_are_actually_used():
    """Chaque permission du registre garde au moins une route (pas de perm morte)."""
    used = set()
    for rel in _guard_bearing_sources():
        used |= _guarded_resources_in(rel)
    unused = KNOWN_RESOURCES - used
    assert not unused, (
        f"Permissions declarees mais jamais utilisees par une garde : {sorted(unused)}"
    )


# ---------------------------------------------------------------------------
# 3. Matrice d'acceptation par domaine (vraie app Flask, decorateurs reels)
# ---------------------------------------------------------------------------

class _Role:
    """Role factice n'accordant QUE la permission admin_<resource>."""

    def __init__(self, resource):
        setattr(self, f"admin_{resource}", True)


class _User(UserMixin):
    """Utilisateur factice encode par son perm (id restituable par le loader)."""

    def __init__(self, perm):
        self.perm = perm
        self.username = f"user-{perm}"
        self.roles = [] if perm == "noroles" else [_Role(perm)]

    def get_id(self):
        return self.perm


def _make_app(tmp_path):
    app = Flask(__name__, template_folder=str(tmp_path))
    app.config["SECRET_KEY"] = "test-secret-1-5"
    app.config["TESTING"] = True

    lm = LoginManager()
    lm.init_app(app)

    @lm.user_loader
    def _load(uid):
        return _User(uid)

    security_bp = Blueprint("security", __name__)

    @security_bp.route("/login")
    def login():
        return "login", 200

    app.register_blueprint(security_bp)

    def _register(resource):
        @app.route(f"/d/{resource}/page", endpoint=f"page_{resource}")
        @require_permission(resource)
        def _page():
            return f"page {resource}", 200

        @app.route(f"/d/{resource}/sub", methods=["POST"], endpoint=f"sub_{resource}")
        @require_permission_api(resource)
        def _sub():
            return jsonify(ok=resource), 200

    for resource in PERMISSION_RESOURCES:
        _register(resource)

    @app.route("/login-as/<perm>", methods=["POST"])
    def login_as(perm):
        login_user(_User(perm))
        return "", 204

    return app


@pytest.fixture
def client(tmp_path):
    tpl_dir = tmp_path / "admin"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "permission_error.html").write_text("{{ error_message }}", encoding="utf-8")
    return _make_app(tmp_path).test_client()


def _other_resource(resource):
    """Une ressource DIFFERENTE, pour jouer le mauvais role."""
    for candidate in PERMISSION_RESOURCES:
        if candidate != resource:
            return candidate
    raise AssertionError("registre trop petit pour le test")


@pytest.mark.parametrize("resource", PERMISSION_RESOURCES)
def test_anonymous_is_refused(client, resource):
    r = client.get(f"/d/{resource}/page")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
    r = client.post(f"/d/{resource}/sub")
    assert r.status_code == 401
    assert r.is_json


@pytest.mark.parametrize("resource", PERMISSION_RESOURCES)
def test_authenticated_without_role_is_forbidden(client, resource):
    client.post("/login-as/noroles")
    assert client.get(f"/d/{resource}/page").status_code == 403
    r = client.post(f"/d/{resource}/sub")
    assert r.status_code == 403
    assert r.is_json


@pytest.mark.parametrize("resource", PERMISSION_RESOURCES)
def test_wrong_role_is_forbidden_including_subroute(client, resource):
    client.post(f"/login-as/{_other_resource(resource)}")
    assert client.get(f"/d/{resource}/page").status_code == 403
    assert client.post(f"/d/{resource}/sub").status_code == 403


@pytest.mark.parametrize("resource", PERMISSION_RESOURCES)
def test_correct_permission_grants_access(client, resource):
    client.post(f"/login-as/{resource}")
    r = client.get(f"/d/{resource}/page")
    assert r.status_code == 200
    assert f"page {resource}".encode() in r.data
    r = client.post(f"/d/{resource}/sub")
    assert r.status_code == 200
    assert r.get_json() == {"ok": resource}
