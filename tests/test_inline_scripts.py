"""Phase 8, point 2 — sortie des scripts inline des gabarits.

Le JavaScript qui vivait dans les gabarits n'est ni mis en cache par le
navigateur, ni analysable par un outil, et il était renvoyé à chaque réponse —
parfois des dizaines de fois dans la même page quand il se trouvait dans le corps
d'une macro appelée en boucle.

Ces tests verrouillent l'acquis :

1. les fichiers extraits existent et sont bien chargés par ``admin/base.html`` ;
2. les gabarits nettoyés ne réintroduisent pas de ``<script>`` inline ;
3. un **cliquet** global : plus AUCUN bloc de JavaScript inline dans les
   gabarits. Les valeurs qui venaient de Jinja passent désormais par des
   attributs ``data-*``, et les données structurées par un îlot
   ``type="application/json"``.
"""

import io
import os
import re

import pytest

_RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_GABARITS = os.path.join(_RACINE, "templates")

_BALISE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.DOTALL | re.IGNORECASE)

#: Nombre de blocs de JavaScript inline encore présents dans les gabarits.
#: Le point 2 les a tous sortis : ce plafond est donc à ZÉRO et ne doit jamais
#: remonter. Les îlots de données `type="application/json"` ne comptent pas —
#: ils ne sont pas du code (voir `_blocs_inline`).
_PLAFOND_INLINE = 0


def _lire(rel):
    return io.open(os.path.join(_RACINE, rel), encoding="utf-8").read()


def _blocs_inline(contenu):
    """Blocs de JavaScript **exécutable** écrits dans le gabarit.

    Sont exclus : les `<script src=...>` (références externes) et les îlots de
    données `type="application/json"` — ces derniers ne sont pas du code, et
    c'est précisément le bon moyen de passer des données du serveur au client.
    """
    blocs = []
    for attrs, corps in _BALISE.findall(contenu):
        attrs_min = attrs.lower()
        if "src=" in attrs_min or not corps.strip():
            continue
        if "type=" in attrs_min and "javascript" not in attrs_min:
            continue          # application/json, text/template…
        blocs.append(corps)
    return blocs


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
    "static/js/admin_macros_forms.js",
    "static/js/admin_backups.js",
    "static/js/admin_dashboard_select.js",
    "static/js/patient_conclusion.js",
])
def test_fichier_extrait_existe_et_non_vide(fichier):
    contenu = _lire(fichier)
    assert len(contenu.strip()) > 0


@pytest.mark.parametrize("fichier", [
    "admin_macros.js", "admin_fragments.js", "admin_macros_forms.js",
    "admin_backups.js", "admin_dashboard_select.js",
])
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
    # On compare les balises <script> elles-memes : le nom du fichier apparait
    # aussi dans les commentaires du gabarit.
    assert contenu.index("js/libs/chart-3.7.0.min.js") < contenu.index("js/admin_stats.js"), (
        "admin_stats.js doit être déclaré APRÈS Chart.js, sinon Chart est "
        "indéfini au moment de son exécution"
    )


def test_stats_ne_depend_plus_d_un_cdn():
    """Les librairies de graphique sont servies localement (phase 8).

    stats.html était la dernière page à charger Chart.js, moment et le plugin
    datalabels depuis cdnjs/jsdelivr : hors ligne ou sous CSP stricte, elle
    était la seule à casser.
    """
    contenu = _lire("templates/admin/stats.html")
    for hote in ("cdnjs.cloudflare.com", "cdn.jsdelivr.net"):
        assert hote not in contenu, f"stats.html dépend encore de {hote}"
    for lib in ("chart-3.7.0.min.js", "moment-2.29.1.min.js",
                "chartjs-adapter-moment-1.0.0.min.js",
                "chartjs-plugin-datalabels-2.2.0.min.js"):
        assert f"js/libs/{lib}" in contenu, f"{lib} n'est pas servi localement"


def test_admin_stats_js_ne_charge_rien_dynamiquement():
    """Plus d'``import()`` distant : le plugin datalabels est chargé en `defer`.

    L'import dynamique pouvait arriver après le premier rendu (étiquettes
    manquantes) ou pas du tout (réseau coupé).
    """
    contenu = _lire("static/js/admin_stats.js")
    assert "import(" not in contenu
    assert "https://" not in contenu


# --- 2. Les gabarits nettoyés le restent ------------------------------------

@pytest.mark.parametrize("gabarit", [
    "templates/admin/macros.html",
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
    "templates/admin/app_backups.html",
    "templates/admin/app_connexion.html",
    "templates/admin/dashboard_select.html",
    "templates/admin/gallery_manage.html",
    "templates/admin/gallery_list_images.html",
    "templates/admin/activity_htmx_table.html",
    "templates/admin/schedule_htmx_table.html",
    "templates/admin/translations_language_add_form.html",
    "templates/admin/translations_languages_htmx_table.html",
    "templates/patient/conclusion_page.html",
    "templates/patient/activity_inactive.html",
    "templates/patient/patient_qr_right_page.html",
    "templates/announce/gallery.html",
    "templates/counter/staff_on_counter.html",
])
def test_gabarit_nettoye_na_plus_de_javascript_inline(gabarit):
    assert _blocs_inline(_lire(gabarit)) == [], (
        f"{gabarit} contient à nouveau du JavaScript inline : le déplacer dans "
        f"un fichier de static/js/. Si du code a besoin d'une valeur du serveur, "
        f"la passer par un attribut data-* plutôt que par de l'interpolation Jinja."
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
    # (le plafond est à 0 : il ne peut plus descendre)


def test_ilot_de_donnees_json_conserve():
    """Le bon motif pour passer des données au client : il doit rester."""
    contenu = _lire("templates/patient/conclusion_page.html")
    assert 'id="print_ui_labels" type="application/json"' in contenu


def test_valeurs_serveur_passent_par_des_attributs_data():
    """Les valeurs Jinja qui étaient interpolées DANS du JavaScript."""
    for gabarit, attribut in (
        ("templates/patient/activity_inactive.html", "data-activity-inactive-delay"),
        ("templates/patient/conclusion_page.html", "data-duration"),
        ("templates/announce/gallery.html", "data-swiper-delay"),
        ("templates/admin/macros.html", "data-restore-table"),
        ("templates/admin/macros.html", "data-copy-colors-page"),
        ("templates/admin/translations_languages_htmx_table.html", "data-flag-target"),
    ):
        assert attribut in _lire(gabarit), f"{attribut} manquant dans {gabarit}"
