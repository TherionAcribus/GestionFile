"""Phase 8, point 2 — sortie des scripts inline des gabarits.

Le JavaScript qui vivait dans les gabarits n'est ni mis en cache par le
navigateur, ni analysable par un outil, et il était renvoyé à chaque réponse —
parfois des dizaines de fois dans la même page quand il se trouvait dans le corps
d'une macro appelée en boucle.

Ces tests verrouillent l'acquis :

1. les fichiers extraits existent et sont bien chargés par ``admin/base.html`` ;
2. les gabarits nettoyés ne réintroduisent pas de ``<script>`` inline ;
3. un **cliquet** global : le nombre de blocs inline restants ne doit pas
   augmenter (il reste des cas dépendant de variables Jinja, à traiter plus tard).
"""

import io
import os
import re

import pytest

_RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_GABARITS = os.path.join(_RACINE, "templates")

_BALISE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.DOTALL | re.IGNORECASE)

#: Nombre de blocs `<script>` inline encore présents dans les gabarits.
#: Ce plafond ne doit que DIMINUER. Les blocs restants dépendent d'expressions
#: Jinja et demandent un pont `data-*` pour être sortis à leur tour.
_PLAFOND_INLINE = 17


def _lire(rel):
    return io.open(os.path.join(_RACINE, rel), encoding="utf-8").read()


def _blocs_inline(contenu):
    return [corps for attrs, corps in _BALISE.findall(contenu)
            if "src=" not in attrs.lower() and corps.strip()]


def _tous_les_gabarits():
    for base, _dirs, fichiers in os.walk(_GABARITS):
        for nom in sorted(fichiers):
            if nom.endswith(".html"):
                chemin = os.path.join(base, nom)
                yield os.path.relpath(chemin, _RACINE).replace(os.sep, "/")


# --- 1. Les fichiers extraits existent et sont chargés ----------------------

@pytest.mark.parametrize("fichier", [
    "static/js/admin_macros.js",
    "static/js/admin_fragments.js",
    "static/js/admin_stats.js",
    "static/js/admin_data.js",
    "static/js/admin_home.js",
])
def test_fichier_extrait_existe_et_non_vide(fichier):
    contenu = _lire(fichier)
    assert len(contenu.strip()) > 0


@pytest.mark.parametrize("fichier", ["admin_macros.js", "admin_fragments.js"])
def test_scripts_partages_charges_une_fois_par_base(fichier):
    """Chargés depuis base.html, donc une seule fois par page."""
    base = _lire("templates/admin/base.html")
    assert base.count(f"js/{fichier}") == 1, (
        f"{fichier} doit être chargé exactement une fois depuis admin/base.html"
    )


@pytest.mark.parametrize("gabarit,fichier", [
    ("templates/admin/stats.html", "admin_stats.js"),
    ("templates/admin/data.html", "admin_data.js"),
    ("templates/admin/admin.html", "admin_home.js"),
])
def test_page_charge_son_script_dans_scripts_end(gabarit, fichier):
    contenu = _lire(gabarit)
    assert "{% block scripts_end %}" in contenu
    assert f"js/{fichier}" in contenu


def test_stats_charge_son_script_apres_ses_dependances():
    """`defer` respecte l'ordre du document : admin_stats.js utilise Chart.js."""
    contenu = _lire("templates/admin/stats.html")
    assert contenu.index("chart.min.js") < contenu.index("admin_stats.js"), (
        "admin_stats.js doit être déclaré APRÈS Chart.js, sinon Chart est "
        "indéfini au moment de son exécution"
    )


# --- 2. Les gabarits nettoyés le restent ------------------------------------

@pytest.mark.parametrize("gabarit", [
    "templates/admin/macros.html",          # ne garde que les blocs à Jinja
    "templates/admin/stats.html",
    "templates/admin/data.html",
    "templates/admin/admin.html",
    "templates/admin/announce_audio.html",
    "templates/admin/announce_audio_gallery.html",
    "templates/admin/patient_page_button_modal_gallery.html",
    "templates/admin/patient_page_button_modal_gallery_for_interface.html",
    "templates/admin/counter_order_counters.html",
    "templates/admin/patient_page_order_buttons.html",
    "templates/admin/translations_languages_order.html",
])
def test_gabarit_nettoye_na_plus_de_script_sans_jinja(gabarit):
    """Un bloc restant est toléré s'il dépend d'une variable Jinja ; un bloc de
    JavaScript pur doit, lui, vivre dans un fichier."""
    jinja = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
    fautifs = [c for c in _blocs_inline(_lire(gabarit)) if not jinja.search(c)]
    assert fautifs == [], (
        f"{gabarit} contient à nouveau du JavaScript inline sans variable Jinja : "
        f"le déplacer dans un fichier de static/js/"
    )


def test_macro_common_js_supprimee():
    """Elle n'existait que pour injecter un <script> ; son contenu est un fichier."""
    macros = _lire("templates/admin/macros.html")
    assert "macro common_js" not in macros
    assert "common_js()" not in _lire("templates/admin/base.html")


def test_reordonnancement_passe_par_un_seul_point_daccroche():
    """Les trois fragments appelaient `sortable()` en ligne ; admin.js le fait
    désormais une fois, après chaque échange HTMX qui amène la liste."""
    admin_js = _lire("static/js/admin.js")
    assert "htmx:afterSettle" in admin_js
    assert "#list_order_buttons" in admin_js
    for gabarit in ("templates/admin/counter_order_counters.html",
                    "templates/admin/patient_page_order_buttons.html",
                    "templates/admin/translations_languages_order.html"):
        assert "sortable();" not in _lire(gabarit)


# --- 3. Cliquet global ------------------------------------------------------

def test_nombre_de_scripts_inline_ne_remonte_pas():
    total = sum(len(_blocs_inline(_lire(rel))) for rel in _tous_les_gabarits())
    assert total <= _PLAFOND_INLINE, (
        f"{total} blocs <script> inline dans les gabarits, contre {_PLAFOND_INLINE} "
        f"attendus au maximum. Placez le nouveau JavaScript dans static/js/."
    )
    if total < _PLAFOND_INLINE:
        pytest.fail(
            f"Bonne nouvelle : il ne reste que {total} blocs inline (plafond "
            f"{_PLAFOND_INLINE}). Abaissez _PLAFOND_INLINE à {total} pour "
            f"verrouiller le progrès.",
            pytrace=False,
        )
