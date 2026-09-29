"""Page /admin/announce refaite : éditeur visuel mis en avant.

Couvre :
- l'éditeur visuel couvre désormais la galerie (durée, transition,
  mélange, taille), avec minimum contrôlé côté serveur ;
- la page : carte « Éditeur visuel — recommandé », mode avancé replié
  (textes/couleurs + galerie), onglets Apparence / Son et voix ;
- les anciennes adresses d'onglets (gallery, googleVoice) restent valides.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

import page_editor
import routes.admin_announce as admin_announce
from page_editor import ADAPTERS

SERVEUR_DIR = Path(__file__).resolve().parents[1]


def _read(rel):
    return (SERVEUR_DIR / rel).read_text(encoding="utf-8")


# --- Éditeur visuel ----------------------------------------------------------------

def test_editeur_couvre_la_galerie():
    fields = {f["key"]: f for f in ADAPTERS["announce"]["components"]["gallery"]["config"]}
    for key in ("announce_infos_display", "announce_infos_display_time", "announce_infos_transition",
                "announce_infos_mix_folders", "announce_infos_width", "announce_infos_height"):
        assert key in fields, key
    assert fields["announce_infos_width"]["min"] == 50
    assert {c[0] for c in fields["announce_infos_transition"]["choices"]} == {
        "slide", "fade", "cube", "coverflow", "flip", "cards"}
    assert "Galerie média" in fields["announce_infos_display_time"]["help"]


def test_minimum_controle_seulement_si_modifie():
    app = Flask(__name__)
    app.config["ANNOUNCE_INFOS_WIDTH"] = 0   # valeur publiée atypique
    from tests.test_page_editor import make_payload
    with app.app_context():
        payload = make_payload("announce")
        payload["config"]["announce_infos_width"] = 10
        with pytest.raises(ValueError, match="minimum 50"):
            page_editor.validate_payload("announce", payload)
        payload["config"]["announce_infos_width"] = 0   # inchangée : pas de refus « minimum »
        try:
            page_editor.validate_payload("announce", payload)
        except ValueError as exc:
            assert "minimum" not in str(exc)


def test_aide_des_champs_affichee_par_l_editeur():
    js = _read("static/js/page_editor.js")
    assert "field.help" in js and "aria-describedby" in js
    assert "field.min" in js


# --- Page ------------------------------------------------------------------------

def test_page_met_en_avant_l_editeur():
    html = _read("templates/admin/announce.html")
    hero = html.index("announce-editor-hero")
    advanced = html.index('<details class="announce-advanced"')
    assert hero < advanced                       # l'éditeur d'abord
    assert 'href="/admin/page-editor/announce"' in html
    assert "Recommandé" in html
    # Le mode avancé regroupe textes/couleurs ET galerie, replié par défaut.
    section = html[advanced:]
    assert "announce_visual.html" in section and "announce_gallery.html" in section
    assert "{% if not editor_enabled or open_advanced %}open{% endif %}" in html
    # Plus d'onglet Galerie séparé.
    assert 'id="gallery-tab"' not in html
    assert "Galerie média" in html


def test_galerie_du_mode_avance_en_francais():
    html = _read("templates/admin/announce_gallery.html")
    assert "Fondu" in html and "Glissement" in html
    assert ">Slide<" not in html and "images'" not in html


@pytest.fixture()
def route_app():
    app = Flask(__name__)
    source = _read("routes/admin_announce.py")
    for name in set(re.findall(r"app\.config\[['\"]([A-Z_]+)['\"]\]", source)):
        app.config[name] = ""
    app.css_variable_manager = SimpleNamespace(get_all_variables=lambda page: {},
                                               get_variable=lambda page, key: "")
    return app


@pytest.mark.parametrize("tab,expected,advanced", [
    ("gallery", "visual", True),
    ("googleVoice", "audio", False),
    ("audio", "audio", False),
    (None, "visual", False),
])
def test_anciennes_adresses_d_onglets(route_app, tab, expected, advanced):
    captured = {}
    view = inspect_unwrap(admin_announce.announce_page)
    with route_app.test_request_context(), \
            patch.object(admin_announce, "render_template", lambda tpl, **kw: captured.update(kw) or ""), \
            patch.object(admin_announce, "get_google_credentials", lambda: None), \
            patch.object(admin_announce, "Language", SimpleNamespace(query=SimpleNamespace(all=lambda: []))):
        view(tab)
    assert captured["active_tab"] == expected
    assert captured["open_advanced"] is advanced


def inspect_unwrap(func):
    """Vue sans le décorateur de permission (testée ailleurs)."""
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func
