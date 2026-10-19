"""Recherche globale de l'administration (palette « Ctrl+K »).

Couvre :

- le noyau pur ``admin_search.py`` : catalogue cohérent avec les routes, les
  permissions et les ancres réellement présentes dans les gabarits ; extraction
  des réglages (libellés explicites, cible ``id="<clé>"`` présente) ;
- le filtrage par permission et la forme publique (aucun champ de contrôle
  d'accès ni valeur de réglage envoyés au navigateur) ;
- la route ``/admin/search/index`` sur application Flask isolée + SQLite ;
- les contrats du gabarit (script chargé, palette accessible, pas de JS inline).
"""

import glob
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import admin_search as s
from params_registry import PARAM_REGISTRY
from permissions_registry import PERMISSIONS

SERVEUR_DIR = Path(__file__).resolve().parents[1]
ADMIN_TEMPLATES = SERVEUR_DIR / "templates" / "admin"


def _read(path):
    return Path(path).read_text(encoding="utf-8")


def _all_admin_templates():
    return {Path(f).name: _read(f) for f in glob.glob(str(ADMIN_TEMPLATES / "*.html"))}


def _declared_route_patterns():
    """Chemins déclarés dans routes/*.py, convertis en expressions régulières."""
    patterns = []
    for f in glob.glob(str(SERVEUR_DIR / "routes" / "*.py")):
        for path in re.findall(r"\.(?:route|get|post|put|delete)\(\s*[\"'](/[^\"']*)[\"']", _read(f)):
            regex = re.sub(r"<[^>]+>", "[^/]+", re.escape(path).replace(r"\<", "<").replace(r"\>", ">"))
            patterns.append(re.compile(f"^{regex}$"))
    return patterns


def _anchor(url):
    match = re.search(r"#focus=(.+)$", url)
    return match.group(1) if match else None


_RESOURCES = {p.resource for p in PERMISSIONS}


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def test_catalogue_entrees_bien_formees():
    for entry in s.CATALOG:
        assert entry["kind"] in s.KIND_LABELS, entry
        assert entry["title"].strip(), entry
        assert entry["url"].startswith("/admin"), entry
        assert entry["icon"].startswith("bi-"), entry
        permission = entry["permission"]
        perms = () if permission is None else (
            (permission,) if isinstance(permission, str) else permission)
        for p in perms:
            assert p in _RESOURCES, f"permission inconnue {p!r} pour {entry['title']}"


def test_catalogue_sans_doublon_de_titre():
    titles = [e["title"] for e in s.CATALOG]
    assert len(titles) == len(set(titles))


def test_catalogue_urls_correspondent_a_des_routes():
    patterns = _declared_route_patterns()
    for entry in s.CATALOG:
        path = entry["url"].split("#")[0].split("?")[0]
        assert any(p.match(path) for p in patterns), f"route absente : {path}"


def test_catalogue_ancres_presentes_dans_les_gabarits():
    """Chaque ``#focus=<id>`` vise un ``id`` réellement rendu."""
    sources = "\n".join(_all_admin_templates().values())
    for entry in s.CATALOG:
        anchor = _anchor(entry["url"])
        if anchor:
            assert f'id="{anchor}"' in sources, f"ancre absente : {anchor} ({entry['title']})"


def test_pages_de_la_barre_laterale_toutes_indexees():
    base = _read(ADMIN_TEMPLATES / "base.html")
    nav = base.split('id="sidebarMenu"')[1].split("</nav>")[0]
    sidebar_urls = set(re.findall(r'href="(/admin[^"]*)"', nav))
    page_urls = {e["url"] for e in s.CATALOG if e["kind"] == s.KIND_PAGE}
    assert sidebar_urls <= page_urls, sidebar_urls - page_urls


# ---------------------------------------------------------------------------
# Réglages extraits des gabarits
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def settings():
    return s.extract_settings(str(ADMIN_TEMPLATES))


def test_extraction_couvre_les_reglages(settings):
    assert len(settings) >= 90
    keys = [_anchor(e["url"]) for e in settings]
    assert len(keys) == len(set(keys)), "un réglage indexé deux fois"
    assert all(k in PARAM_REGISTRY for k in keys)


def test_reglages_ont_des_titres_explicites(settings):
    for entry in settings:
        assert not s.is_generic_label(entry["title"]), entry


def test_corrections_de_libelles_portent_sur_des_cles_connues():
    assert set(s.LABEL_OVERRIDES) <= set(PARAM_REGISTRY)


def test_gabarits_rattaches_existent_et_aucun_oubli():
    templates = _all_admin_templates()
    for name in s.TEMPLATE_LOCATIONS:
        assert name in templates, name
    for name, source in templates.items():
        if name in s.TEMPLATE_LOCATIONS:
            continue
        keys = {k for k, _ in s._labels_in(source) if k in PARAM_REGISTRY}
        assert not keys, f"{name} contient des réglages ({sorted(keys)}) : le rattacher dans TEMPLATE_LOCATIONS"


def _macros_posant_id():
    """Macros de macros.html rendant ``id="{{ <1er paramètre> }}"`` (directement
    ou via une macro appelée avec ce paramètre)."""
    src = _read(ADMIN_TEMPLATES / "macros.html")
    macros = {m.group(1): (m.group(2).split(",")[0].strip(), m.group(3)) for m in re.finditer(
        r"{%-?\s*macro\s+(\w+)\(([^)]*)\)\s*-?%}(.*?){%-?\s*endmacro", src, re.S)}

    def sets_id(name, seen=()):
        param, body = macros[name]
        if not param.isidentifier():
            return False
        if re.search(r'id="{{\s*' + param + r'\s*}}"', body):
            return True
        return any(c in macros and c not in seen and sets_id(c, (*seen, name))
                   for c in re.findall(r"\b(\w+)\(\s*" + param + r"\b", body))
    return {name for name in macros if sets_id(name)}


def test_chaque_reglage_a_sa_cible_de_saut(settings):
    id_macros = _macros_posant_id()
    by_key = {}
    for name in s.TEMPLATE_LOCATIONS:
        source = _read(ADMIN_TEMPLATES / name)
        for m in s._MACRO_CALL.finditer(source):
            if m.group(1) in id_macros:
                by_key.setdefault(m.group(2), True)
        for key in re.findall(r'\bid="([a-z0-9_]+)"', source):
            by_key.setdefault(key, True)
    for entry in settings:
        key = _anchor(entry["url"])
        assert key in by_key, f"aucun id=\"{key}\" rendu pour « {entry['title']} »"


def test_reglages_exigent_page_et_permission_du_reglage(settings):
    mail = next(e for e in settings if e["url"].endswith("#focus=mail_server"))
    assert mail["permission_mode"] == "all"
    assert "app" in mail["permission"]
    security = next(e for e in settings if e["url"].endswith("#focus=security_login_counter"))
    assert set(security["permission"]) == {"security_view", PARAM_REGISTRY["security_login_counter"].permission}


def test_aucune_valeur_de_reglage_indexee(settings):
    for entry in settings:
        assert set(entry) == {"kind", "title", "url", "permission", "permission_mode",
                              "section", "keywords", "icon"}


# ---------------------------------------------------------------------------
# Filtrage, forme publique, éléments vivants
# ---------------------------------------------------------------------------

def test_is_allowed_selon_la_forme_de_permission():
    def can(resource):
        return resource in {"stats", "app"}
    assert s.is_allowed({"permission": None}, can)
    assert s.is_allowed({"permission": "app"}, can)
    assert not s.is_allowed({"permission": "security"}, can)
    assert s.is_allowed({"permission": ("stats", "performance")}, can)
    assert not s.is_allowed({"permission": ("app", "security"), "permission_mode": "all"}, can)
    assert s.is_allowed({"permission": ("app", "stats"), "permission_mode": "all"}, can)


def test_forme_publique_sans_controle_d_acces():
    entries = s.visible_entries(s.static_index(str(ADMIN_TEMPLATES)), lambda r: True)
    assert entries
    for entry in entries:
        assert "permission" not in entry and "permission_mode" not in entry
        assert entry["kind_label"]


def test_compte_sans_droit_ne_voit_que_les_entrees_communes():
    entries = s.visible_entries(s.static_index(str(ADMIN_TEMPLATES)), lambda r: False)
    assert {e["url"] for e in entries} == {
        e["url"] for e in s.CATALOG if e["permission"] is None}


def test_entity_entry_refuse_une_ancre_douteuse():
    ok = s.entity_entry("Vaccin", "/admin/activity", "edit_a3", "Activités")
    assert ok["url"] == "/admin/activity#focus=edit_a3"
    bad = s.entity_entry("x", "/admin/activity", 'a"><script>', "Activités")
    assert bad["url"] == "/admin/activity"
    assert s.entity_entry("  ", "/admin/staff", "", "Équipe")["title"] == "(sans nom)"


@pytest.mark.parametrize("query, expected_title", [
    ("sauvegarde", "Sauvegardes"),
    ("backup", "Sauvegardes"),
    ("smtp", "E-mails"),
    ("purge", "Remise à zéro quotidienne de la file"),
    ("ecran annonce", "Écran d'annonce"),
    ("SPOTIFY", "Connexion à Spotify"),
    ("mot de passe comptoir", "Mot de passe sur les pages 'Comptoir'"),
    ("tts", "Moteur de voix (Google, gTTS)"),
    ("largeur ticket", "Largeur d'impression du ticket"),
    ("qr", "QR code"),
])
def test_recherches_usuelles_aboutissent(query, expected_title):
    index = s.static_index(str(ADMIN_TEMPLATES))
    titles = {e["title"] for e in index if s.matches(e, query)}
    assert expected_title in titles


def test_normalisation_sans_accents():
    assert s.normalize("Écran d'ANNONCE") == "ecran d'annonce"


# ---------------------------------------------------------------------------
# Route /admin/search/index
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    import routes.admin_search as admin_search_route
    from datetime import time

    from models import Activity, AlgoRule, Counter, Language, Pharmacist, Role, User, db

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
        return User.query.filter_by(fs_uniquifier=user_id).first()

    app.register_blueprint(admin_search_route.admin_search_bp)

    with app.app_context():
        db.create_all()
        full = Role(name="admin", admin_security=True, admin_app=True, admin_activity=True,
                    admin_staff=True, admin_counter=True, admin_patient=True,
                    admin_algo=True, admin_translation=True)
        kiosk = Role(name="borne", admin_patient=True)
        alice = User(username="alice", email="a@a.a", password=generate_password_hash("x"), active=True)
        alice.roles.append(full)
        bob = User(username="bob", email="b@b.b", password=generate_password_hash("x"), active=True)
        bob.roles.append(kiosk)
        vaccination = Activity(name="Vaccination", letter="V")
        db.session.add_all([
            full, kiosk, alice, bob, vaccination,
            AlgoRule(name="Urgences d'abord", activity=vaccination,
                     start_time=time(8), end_time=time(20)),
            Language(code="en", name="Anglais", translation="English"),
            Activity(name="Demande Dr Martin", letter="M", is_staff=True),
            Counter(name="Comptoir 1", sort_order=1),
            Pharmacist(name="Claire Dupont", initials="CD"),
        ])
        db.session.commit()

    yield app

    with app.app_context():
        db.session.remove()
        db.drop_all()


def _client_as(app, username):
    from models import User
    client = app.test_client()
    with app.app_context():
        uniquifier = User.query.filter_by(username=username).one().fs_uniquifier
    with client.session_transaction() as session:
        session["_user_id"] = uniquifier
        session["_fresh"] = True
    return client


def test_route_anonyme_refusee(app):
    assert app.test_client().get("/admin/search/index").status_code == 401


def test_route_admin_complet(app):
    response = _client_as(app, "alice").get("/admin/search/index")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    entries = response.get_json()["entries"]
    by_title = {e["title"]: e for e in entries}
    assert by_title["Vaccination"]["url"].startswith("/admin/activity?tab=activity#focus=edit_a")
    assert by_title["Demande Dr Martin"]["url"].startswith("/admin/activity?tab=staff#focus=edit_s")
    assert by_title["Comptoir 1"]["url"].startswith("/admin/counter#focus=edit_counter_")
    assert by_title["Claire Dupont"]["url"].startswith("/admin/staff#focus=edit_staff_")
    assert "bob" in by_title  # comptes visibles avec la permission Sécurité
    assert by_title["Urgences d'abord"]["url"].startswith("/admin/algo#focus=rule_edit_")
    assert by_title["Anglais"]["url"].startswith("/admin/translations#focus=name-")
    assert by_title["borne"]["url"].startswith("/admin/security?tab=roles#focus=role-item-")
    assert "Sauvegardes" in by_title and "Serveur SMTP" in by_title
    for entry in entries:
        assert "permission" not in entry


def test_route_filtre_selon_les_droits(app):
    entries = _client_as(app, "bob").get("/admin/search/index").get_json()["entries"]
    titles = {e["title"] for e in entries}
    urls = {e["url"].split("#")[0].split("?")[0] for e in entries}
    # Borne patient : visible ; tout le reste : jamais envoyé.
    assert "Borne patient" in titles and "Largeur d'impression du ticket" in titles
    assert not urls & {"/admin/security", "/admin/app", "/admin/activity", "/admin/counter",
                       "/admin/staff", "/admin/queue"}
    assert not titles & {"Vaccination", "Comptoir 1", "Claire Dupont", "alice", "bob",
                         "Urgences d'abord", "Anglais", "admin", "borne"}


def test_route_resiste_a_une_base_en_erreur(app):
    with patch("routes.admin_search.Activity") as broken:
        broken.query.order_by.side_effect = RuntimeError("base indisponible")
        response = _client_as(app, "alice").get("/admin/search/index")
    assert response.status_code == 200
    titles = {e["title"] for e in response.get_json()["entries"]}
    # Seule la source en erreur manque : pages, réglages et autres éléments restent.
    assert "Sauvegardes" in titles and "Comptoir 1" in titles and "Anglais" in titles
    assert "Vaccination" not in titles


@pytest.mark.parametrize("template, anchor", [
    ("activity_htmx_table.html", 'id="edit_{{ p }}"'),
    ("counter_htmx_table.html", 'id="edit_counter_{{ counter.id }}"'),
    ("staff_htmx_table.html", 'id="edit_staff_{{ member.id }}"'),
    ("algo_htmx_table.html", 'id="rule_edit_{{ rule.id }}"'),
    ("translations_languages_htmx_table.html", 'id="name-{{ language.id }}"'),
    ("security_htmx_role_table.html", 'id="role-item-{{ role.id }}"'),
    ("security_htmx_table.html", 'id="user-{{ user.id }}"'),
])
def test_ancres_des_elements_vivants_rendues(template, anchor):
    """Les repères construits par routes/admin_search.py existent dans les
    gabarits qui affichent ces éléments (sinon le saut direct tombe à vide)."""
    assert anchor in _read(ADMIN_TEMPLATES / template)


# ---------------------------------------------------------------------------
# Gabarit et script
# ---------------------------------------------------------------------------

def test_base_charge_la_palette():
    base = _read(ADMIN_TEMPLATES / "base.html")
    assert "js/admin_search.js" in base
    assert base.index("js/libs/bootstrap.bundle.min.js") < base.index("js/admin_search.js")
    assert 'id="admin-search-modal"' in base
    assert 'role="combobox"' in base and 'role="listbox"' in base
    assert base.count("data-admin-search-open") == 2  # barre latérale + mobile
    assert 'aria-keyshortcuts="Control+K"' in base


def test_script_sans_insertion_html_brute():
    """Les titres viennent en partie de la base : jamais d'innerHTML."""
    js = _read(SERVEUR_DIR / "static" / "js" / "admin_search.js")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js
    assert "/admin/search/index" in js
    assert "#focus=" in js or "focus=" in js
