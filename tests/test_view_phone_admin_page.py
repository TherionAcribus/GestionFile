"""Page /admin/phone refaite + alerte « quitter la page » supprimée.

Couvre :
- la cause de l'alerte : le bouton « appliquer à toutes les lignes »
  (macro parent_number_input_bloc) est toujours actif ; le garde-fou des
  modifications non enregistrées le prenait pour une saisie en attente ;
- la graisse des lignes enregistrée sans « px » (valeur CSS invalide) ;
- l'éditeur téléphone activé par défaut et complété (graisse du message
  spécifique, 400 dans les thèmes) ;
- la page : carte « Éditeur visuel », mode avancé replié, mention
  trompeuse « Pas utilisé... » retirée.
"""

from pathlib import Path

from flask import Flask

from config import Config
from page_editor import ADAPTERS, builtin_themes

SERVEUR_DIR = Path(__file__).resolve().parents[1]


def _read(rel):
    return (SERVEUR_DIR / rel).read_text(encoding="utf-8")


def test_alerte_depart_page_bouton_toujours_actif_ignore():
    macros = _read("templates/admin/macros.html")
    bloc = macros[macros.index("macro parent_number_input_bloc"):]
    bloc = bloc[:bloc.index("endmacro")]
    assert "data-unsaved-ignore" in bloc
    js = _read("static/js/admin_unsaved_changes.js")
    assert ':not([data-unsaved-ignore])' in js


def test_graisse_des_lignes_sans_px():
    for tpl in ("phone_confirmation_page.html", "phone_your_turn_page.html"):
        src = _read(f"templates/admin/{tpl}")
        line = next(l for l in src.splitlines() if "_lines_font_weight" in l)
        assert 'unit=""' in line and 'unit="px"' not in line, tpl


def test_editeur_telephone_active_et_complet():
    pages = {p.strip() for p in Config.PAGE_EDITOR_ENABLED_PAGES.split(",")}
    assert {"announce", "patient", "phone"} <= pages
    specific = {f["key"]: f for f in ADAPTERS["phone"]["components"]["specific"]["css"]}
    assert specific["phone_specific_message_font_weight"]["type"] == "number"
    with Flask(__name__).app_context():
        for theme in builtin_themes("phone"):
            assert theme["snapshot"]["css"]["phone_specific_message_font_weight"] == "400", theme["id"]


def test_page_met_en_avant_l_editeur():
    html = _read("templates/admin/phone.html")
    assert html.index("announce-editor-hero") < html.index('<details class="announce-advanced"')
    assert 'href="/admin/page-editor/phone"' in html and "Recommandé" in html
    assert 'class="advanced-editor-page"' in html and 'class="row advanced-editor-workspace"' in html
    assert "Pas utilisé" not in _read("templates/admin/phone_confirmation_page.html")
