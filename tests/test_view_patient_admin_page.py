"""Page /admin/patient refaite : éditeur visuel mis en avant.

Couvre :
- l'éditeur de la borne activé par défaut (announce,patient) ;
- les réglages ajoutés à l'éditeur : écran de validation, confirmation,
  contour/fond du texte des boutons ; les thèmes gardent un fond de texte
  transparent ;
- la page : carte « Éditeur visuel », mode avancé replié, onglet Interface
  (réglages jamais lus) supprimé ;
- l'onglet demandé par l'URL enfin transmis au gabarit.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask

import routes.admin_patient as admin_patient
from config import Config
from page_editor import ADAPTERS, builtin_themes

SERVEUR_DIR = Path(__file__).resolve().parents[1]


def _read(rel):
    return (SERVEUR_DIR / rel).read_text(encoding="utf-8")


def test_editeur_borne_active_par_defaut():
    pages = {p.strip() for p in Config.PAGE_EDITOR_ENABLED_PAGES.split(",")}
    assert {"announce", "patient"} <= pages


def test_editeur_couvre_l_ecran_de_validation():
    buttons = ADAPTERS["patient"]["components"]["buttons"]
    config = {f["key"] for f in buttons["config"]}
    for key in ("page_patient_validation_message", "page_patient_interface_validate_print",
                "page_patient_interface_validate_scan", "page_patient_interface_validate_cancel",
                "page_patient_interface_scan_explanation", "page_patient_confirmation_message",
                "page_patient_button_print_ticket_display_picture",
                "page_patient_button_cancel_display_picture"):
        assert key in config, key
    css = {f["key"] for f in buttons["css"]}
    for prefix in ("circle_button", "square_button", "square_cancel_button", "validation_button"):
        assert f"{prefix}_text_border_color" in css
        assert f"{prefix}_text_background_color" in css


def test_mode_avance_sans_reglage_orphelin():
    """Tout réglage de config du mode avancé « Textes » est aussi dans l'éditeur."""
    src = _read("templates/admin/page_patient_page.html")
    advanced = set(re.findall(r'\(\s*"(page_patient_[a-z_]+)"', src)) | set(
        re.findall(r'"key": "(page_patient_[a-z_]+)"', src))
    editor = {f["key"] for c in ADAPTERS["patient"]["components"].values() for f in c["config"]}
    assert advanced - editor == set()


def test_themes_gardent_un_fond_de_texte_transparent():
    app = Flask(__name__)
    with app.app_context():
        for theme in builtin_themes("patient"):
            css = theme["snapshot"]["css"]
            for prefix in ("circle_button", "square_button", "square_cancel_button", "validation_button"):
                assert css[f"{prefix}_text_background_color"] == "transparent", (theme["id"], prefix)


def test_page_met_en_avant_l_editeur():
    html = _read("templates/admin/patient_page.html")
    assert html.index("announce-editor-hero") < html.index('<details class="announce-advanced"')
    assert 'href="/admin/page-editor/patient"' in html and "Recommandé" in html
    assert "page_patient_interface.html" not in html
    assert not (SERVEUR_DIR / "templates/admin/page_patient_interface.html").exists()
    assert "hx-confirm" in html                       # recharger les bornes


@pytest.mark.parametrize("path_tab,query_tab,expected", [
    ("buttons", None, "buttons"),
    (None, "buttons", "buttons"),
    ("interface", None, "visual"),
    ("text", None, "visual"),
    (None, None, "visual"),
    ("pirate", None, "visual"),
])
def test_onglet_transmis_au_gabarit(path_tab, query_tab, expected):
    app = Flask(__name__)
    for name in set(re.findall(r"app\.config\[['\"]([A-Z_]+)['\"]\]", _read("routes/admin_patient.py"))):
        app.config[name] = ""
    app.css_variable_manager = SimpleNamespace(get_variable=lambda page, key: "",
                                               get_all_variables=lambda page: {})
    view = admin_patient.admin_patient
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__
    captured = {}
    empty = SimpleNamespace(query=SimpleNamespace(all=lambda: []))
    query = f"/?tab={query_tab}" if query_tab else "/"
    with app.test_request_context(query), \
            patch.object(admin_patient, "render_template", lambda tpl, **kw: captured.update(kw) or ""), \
            patch.object(admin_patient, "Button", empty), \
            patch.object(admin_patient, "Activity", empty), \
            patch.object(admin_patient, "Language", empty):
        view(path_tab)
    assert captured["active_tab"] == expected
