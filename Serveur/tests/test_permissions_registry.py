"""Point 1.5 — Source de vérité unique des permissions.

Trois familles de vérifications, exécutables **sans MySQL ni serveur** :

1. **Cohérence du modèle de permissions** : le registre
   (``permissions_registry.PERMISSIONS``), les colonnes ``admin_*`` du modèle
   ``Role`` et les gardes ``require_permission('<resource>')`` disséminées dans
   le code doivent tous s'accorder. En particulier, les permissions fantômes
   ``admin_music`` / ``admin_dashboard`` ne réapparaissent pas et les
   permissions réelles (``music_play``, ``music_options``, ``staff``, ``phone``,
   ``announce``, ``patient``, ``gallery``…) sont bien exposées.

2. **Détecteur de garde inconnue** : toute route ``/admin`` (blueprints
   ``routes/admin_*`` et ``app.py``) qui pose un ``require_permission*`` doit
   viser une ressource **connue** du registre. Une nouvelle route gardée par une
   permission inexistante fait échouer ce test.

3. **Matrice d'acceptation** par domaine, sur une vraie app Flask, en réutilisant
   les décorateurs de production. Pour chaque permission du registre :
   anonyme → refus ; connecté sans rôle → 403 ; connecté avec le mauvais rôle →
   403 (y compris sur une **sous-route**, pas de contournement) ; connecté avec
   la bonne permission → succès.
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
# 1. Cohérence : registre <-> modèle <-> gabarits
# ---------------------------------------------------------------------------

def test_registry_matches_model_columns_exactly():
    """Le registre décrit EXACTEMENT les colonnes admin_* du modèle Role."""
    from models import Role  # import local : évite d'exiger MySQL au chargement
    model_fields = {c.name for c in Role.__table__.columns
                    if c.name.startswith("admin_")}
    assert model_fields == set(PERMISSION_FIELDS), (
        "Le registre de permissions a divergé du modèle Role.\n"
        f"  seulement dans le modèle : {sorted(model_fields - set(PERMISSION_FIELDS))}\n"
        f"  seulement dans le registre : {sorted(set(PERMISSION_FIELDS) - model_fields)}"
    )


def test_registry_has_no_duplicates():
    assert len(PERMISSION_RESOURCES) == len(set(PERMISSION_RESOURCES))
    assert len(PERMISSION_FIELDS) == len(set(PERMISSION_FIELDS))


def test_phantom_permissions_are_gone():
    """Les permissions inexistantes du modèle ne reviennent pas dans le registre."""
    assert "music" not in KNOWN_RESOURCES
    assert "dashboard" not in KNOWN_RESOURCES
    assert "admin_music" not in PERMISSION_FIELDS
    assert "admin_dashboard" not in PERMISSION_FIELDS


def test_real_permissions_are_exposed():
    """Les domaines jadis absents du formulaire sont bien présents."""
    for resource in ("music_play", "music_options", "staff", "phone",
                     "announce", "patient", "gallery"):
        assert resource in KNOWN_RESOURCES, f"{resource} devrait être exposé"


def test_every_permission_is_fully_described():
    """Chaque entrée porte libellé, description, catégorie et risque valides."""
    valid_risks = {"high", "medium", "low"}
    for perm in PERMISSIONS:
        assert perm.label.strip(), f"{perm.resource}: libellé manquant"
        assert perm.description.strip(), f"{perm.resource}: description manquante"
        assert perm.category.strip(), f"{perm.resource}: catégorie manquante"
        assert perm.risk in valid_risks, f"{perm.resource}: risque invalide"
        assert perm.field == f"admin_{perm.resource}"


def test_add_role_form_no_longer_hardcodes_permissions():
    """Le gabarit d'ajout génère les cases depuis le registre (plus de liste en dur)."""
    tpl = _read("templates/admin/security_add_role_form.html")
    # Les permissions fantômes ont disparu du gabarit.
    assert 'data-permission="admin_music"' not in tpl
    assert 'data-permission="admin_dashboard"' not in tpl
    # Les cases sont générées par une boucle sur le registre.
    assert "permissions_by_category" in tpl
    assert "perm.field" in tpl


# ---------------------------------------------------------------------------
# 2. Détecteur de garde inconnue : toute garde vise une ressource du registre
# ---------------------------------------------------------------------------

_GUARD_CALL_RE = re.compile(
    r"require_permission(?:_api|_dashboard)?\(\s*['\"]([a-z_]+)['\"]")


def _guarded_resources_in(rel):
    """Ressources visées par un require_permission* dans le fichier ``rel``."""
    return set(_GUARD_CALL_RE.findall(_read(rel)))


def _guard_bearing_sources():
    """Sources de production susceptibles de poser des gardes /admin."""
    yield "app.py"
    for name in os.listdir(_ROUTES_DIR):
        if name.startswith("admin_") and name.endswith(".py"):
            yield os.path.join("routes", name)


def test_all_guards_reference_known_permissions():
    """Une route /admin gardée par une permission inconnue fait échouer ce test.

    C'est le garde-fou demandé : ajouter une route avec
    ``@require_permission('nouveau_domaine')`` sans déclarer ``nouveau_domaine``
    dans le registre casse le build.
    """
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
        f"Permissions déclarées mais jamais utilisées par une garde : {sorted(unused)}"
    )


# ---------------------------------------------------------------------------
# 3. Matrice d'acceptation par domaine (vraie app Flask, décorateurs réels)
# ---------------------------------------------------------------------------

class _Role:
    """Rôle factice n'accordant QUE la permission ``admin_<resource>``."""

    def __init__(self, resource):
        setattr(self, f"admin_{resource}", True)


class _User(UserMixin):
    """Utilisateur factice encodé par son ``perm`` (id restituable par le loader).

    - ``perm == "noroles"`` : connecté sans aucun rôle ;
    - sinon : rôle unique accordant ``admin_<perm>``.
    """

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

    # Cible de la redirection PAGE des anonymes.
    security_bp = Blueprint("security", __name__)

    @security_bp.route("/login")
    def login():
        return "login", 200

    app.register_blueprint(security_bp)

    # Deux routes par domaine : une PAGE et une SOUS-ROUTE API, chacune gardée
    # indépendamment par la permission du domaine.
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
    # Gabarit minimal pour la page d'erreur 403 (aucune écriture dans le dépôt).
    tpl_dir = tmp_path / "admin"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "permission_error.html").write_text("{{ error_message }}", encoding="utf-8")
    return _make_app(tmp_path).test_client()


def _other_resource(resource):
    """Une ressource DIFFÉRENTE, pour jouer le « mauvais rôle »."""
    for candidate in PERMISSION_RESOURCES:
        if candidate != resource:
            return candidate
    raise AssertionError("registre trop petit pour le test")


@pytest.mark.parametrize("resource", PERMISSION_RESOURCES)
def test_anonymous_is_refused(client, resource):
    # PAGE : redirection vers la connexion.
    r = client.get(f"/d/{resource}/page")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
    # SOUS-ROUTE API : 401 JSON.
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
    # Connecté avec une permission d'un AUTRE domaine : aucun accès, ni page ni
    # sous-route. Prouve l'absence de contournement direct sur les sous-routes.
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
