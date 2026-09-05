"""Phase 8, point 1 — découpage de admin.js par page.

``admin.js`` faisait 2014 lignes et était chargé sur **chaque** page
d'administration depuis ``admin/base.html``. Plus de la moitié ne servait qu'aux
pages d'apparence : sélecteurs de couleur, copie de palette entre pages, saisie
d'un entier appliqué à plusieurs variables CSS. Ce bloc est extrait dans
``admin_colors.js``, chargé par les trois seules pages concernées.

Ce que ces tests verrouillent :

1. ``admin_colors.js`` reste chargé par ces trois pages, et **pas** globalement
   (sinon le découpage n'aurait servi à rien).
2. Aucune référence pendante : toute fonction appelée par ``admin_macros.js``
   — lui, chargé globalement — est définie quelque part.
3. **Aucune fonction définie deux fois** dans les fichiers JS d'administration.
   Cet invariant a trouvé ``getSelectedVariables``, défini à l'identique aux
   lignes 1258 et 1850 de l'ancien admin.js : la seconde écrasait
   silencieusement la première.
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_JS = os.path.join(_SERVEUR, "static", "js")

# Fichiers JS propres à l'administration (hors bibliothèques tierces).
_FICHIERS_ADMIN = [
    "admin.js", "admin_colors.js", "admin_macros.js", "admin_macros_forms.js",
    "admin_fragments.js", "admin_backups.js", "admin_dashboard_select.js",
    "admin_data.js", "admin_home.js", "admin_stats.js", "admin_unsaved_changes.js",
    "admin_gallery.js", "admin_button_gallery.js", "admin_flag_upload.js",
]

# Blocs extraits de admin.js -> (fichier, pages qui doivent le charger).
# Chaque bloc est une delegation posee sur `document` : elle couvre donc aussi
# les fragments injectes par HTMX apres le chargement de la page.
BLOCS_EXTRAITS = [
    ("admin_colors.js", ["announce.html", "patient_page.html", "phone.html"]),
    ("admin_gallery.js", ["gallery.html"]),
    ("admin_button_gallery.js", ["patient_page.html"]),
    ("admin_flag_upload.js", ["translations.html"]),
]

# Pages incluant les fragments qui appellent les macros de couleur / d'entier.
PAGES_COULEUR = ["announce.html", "patient_page.html", "phone.html"]


def _lire(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _fonctions(source):
    """Noms des fonctions et constantes déclarées au premier niveau."""
    return re.findall(r"^(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)",
                      source, re.M)


# ---------------------------------------------------------------------------
# 1. Le découpage tient
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("page", PAGES_COULEUR)
def test_page_couleur_charge_admin_colors(page):
    """Les trois pages d'apparence déclarent admin_colors.js."""
    contenu = _lire(f"templates/admin/{page}")
    assert "js/admin_colors.js" in contenu, (
        f"{page} inclut un fragment de personnalisation CSS mais ne charge pas "
        "admin_colors.js")
    assert "{% block scripts_end %}" in contenu


@pytest.mark.parametrize("fichier,pages", BLOCS_EXTRAITS)
def test_bloc_extrait_charge_par_ses_pages(fichier, pages):
    """Chaque bloc extrait est declare par les pages qui en ont besoin."""
    for page in pages:
        contenu = _lire(f"templates/admin/{page}")
        assert f"js/{fichier}" in contenu, f"{page} ne charge pas {fichier}"
        assert "{% block scripts_end %}" in contenu


@pytest.mark.parametrize("fichier,_pages", BLOCS_EXTRAITS)
def test_bloc_extrait_pas_charge_globalement(fichier, _pages):
    """base.html ne doit charger aucun bloc extrait : c'est l'objet du découpage."""
    base = _lire("templates/admin/base.html")
    assert fichier not in base, (
        f"{fichier} rechargé globalement : le découpage perd son intérêt")


def test_admin_colors_pas_charge_globalement():
    """base.html garde en revanche les deux fichiers réellement globaux."""
    base = _lire("templates/admin/base.html")
    # admin.js et admin_macros.js, eux, restent globaux.
    assert "js/admin.js" in base
    assert "js/admin_macros.js" in base


def test_admin_js_ne_contient_plus_le_bloc_couleurs():
    """Le code déplacé ne doit pas subsister dans admin.js."""
    admin = _lire("static/js/admin.js")
    for nom in ("colorMappings", "cssNamedColors", "pageColorRoles",
                "numberMappings", "initColorPickers", "copyColorsFromPage"):
        assert nom not in admin, f"{nom} est encore dans admin.js"


def test_admin_js_a_bien_diminue():
    """Cliquet : admin.js ne doit pas regrossir jusqu'à sa taille d'origine.

    2014 lignes avant le découpage. Le plafond laisse de la marge pour des
    évolutions normales tout en signalant un retour en arrière.
    """
    lignes = len(_lire("static/js/admin.js").splitlines())
    assert lignes < 1050, (
        f"admin.js est remonté à {lignes} lignes (849 après le découpage) : "
        "le nouveau code global mérite peut-être son propre fichier de page")


# ---------------------------------------------------------------------------
# 2. Aucune référence pendante
# ---------------------------------------------------------------------------

def test_aucun_appel_pendant_depuis_admin_macros():
    """admin_macros.js est global : ce qu'il appelle doit exister quelque part.

    Il n'invoque ces fonctions que depuis des gestionnaires d'évènements liés
    aux éléments produits par les macros de couleur — donc jamais sur une page
    qui ne charge pas admin_colors.js. Mais un nom mal orthographié ou un
    déplacement incomplet resterait invisible jusqu'au clic de l'utilisateur.
    """
    macros = _lire("static/js/admin_macros.js")
    connus = set()
    for nom in _FICHIERS_ADMIN:
        chemin = os.path.join(_JS, nom)
        if os.path.exists(chemin):
            connus.update(_fonctions(_lire(f"static/js/{nom}")))

    # Fonctions appelées par admin_macros.js et définies dans l'un des deux
    # fichiers issus du découpage : ce sont celles que le déplacement menaçait.
    surveillees = [
        "insertPlaceholder", "handleParentKeyPress", "handleColorChange",
        "handleColorAfterRequest", "selectAllVariables", "deselectAllVariables",
        "invertSelection", "selectAllNumberVariables",
        "deselectAllNumberVariables", "invertNumberSelection",
        "applyNumberToAll", "copyColorsFromPage",
    ]
    manquantes = [f for f in surveillees
                  if re.search(r"\b" + f + r"\s*\(", macros) and f not in connus]
    assert not manquantes, (
        "admin_macros.js appelle des fonctions définies nulle part : "
        + ", ".join(manquantes))


# ---------------------------------------------------------------------------
# 3. Pas de définition en double
# ---------------------------------------------------------------------------

def test_aucune_fonction_definie_deux_fois():
    """Une même fonction ne doit pas être déclarée deux fois.

    Régression : ``getSelectedVariables`` était défini à l'identique deux fois
    dans admin.js. Inoffensif ici (corps identiques), mais la seconde
    déclaration écrasait la première sans le moindre avertissement — si elles
    avaient divergé, le comportement aurait dépendu de l'ordre du fichier.
    """
    par_nom = {}
    for nom in _FICHIERS_ADMIN:
        chemin = os.path.join(_JS, nom)
        if not os.path.exists(chemin):
            continue
        source = _lire(f"static/js/{nom}")
        for i, ligne in enumerate(source.splitlines(), 1):
            m = re.match(r"function\s+([A-Za-z_$][\w$]*)\s*\(", ligne)
            if m:
                par_nom.setdefault(m.group(1), []).append(f"{nom}:{i}")

    doublons = {n: p for n, p in par_nom.items() if len(p) > 1}
    assert not doublons, "fonctions déclarées plusieurs fois :\n" + "\n".join(
        f"  {n} -> {', '.join(p)}" for n, p in sorted(doublons.items()))
