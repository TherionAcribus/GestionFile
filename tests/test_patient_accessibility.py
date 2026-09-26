"""Point 8 — accessibilité des pages patient traduites.

La borne affiche du contenu traduit mais déclarait ``lang="fr"`` en dur —
et le téléphone ``lang="en"`` : un lecteur d'écran prononçait chaque page
avec la mauvaise langue. Les drapeaux étaient des ``<img hx-get>``
impossibles à atteindre au clavier, et une langue sans image de drapeau
n'était tout simplement pas sélectionnable.

Verrouillé ici :

1. ``<html lang>`` suit la langue réellement servie (session pour la
   borne, ``language_code`` validé pour le téléphone) ;
2. chaque langue est un ``<button>`` focusable avec ``aria-label`` traduit,
   ``lang`` du nom annoncé, et un libellé texte quand aucun drapeau
   n'existe ;
3. le changement de langue met aussi à jour ``document.documentElement``
   et ne réinjecte plus le document complet dans ``#main``.
"""

import os
import re

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _source(*parts):
    with open(os.path.join(_SERVEUR, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_page_borne_declare_la_langue_de_session():
    source = _source("templates", "patient", "patient_front_page.html")
    assert '<html lang="{{ session.get(\'language_code\', \'fr\') }}">' in source


def test_page_telephone_declare_la_langue_validee():
    """Régression : le gabarit déclarait lang="en" alors que le contenu est
    servi dans la langue du patient (ou le repli français)."""
    source = _source("templates", "patient", "phone.html")
    assert '<html lang="{{ language_code or \'fr\' }}">' in source


def test_drapeaux_sont_de_vrais_boutons():
    source = _source("templates", "patient", "patient_front_page.html")
    # Plus d'image directement cliquable via htmx.
    assert not re.search(r'<img[^>]*hx-get', source)
    boutons = re.findall(r'<button[^>]*class="flag-button"[^>]*>', source)
    assert boutons, "le sélecteur de langue doit contenir des <button>"
    bouton = boutons[0]
    assert 'type="button"' in bouton
    assert 'aria-label="{{ language.translation }}"' in bouton
    # Le nom est annoncé dans sa propre langue (ex. "English" dit en anglais).
    assert 'lang="{{ language.code }}"' in bouton


def test_langue_sans_drapeau_reste_selectionnable():
    """Avant : `{% if language.flag_url %}` masquait toute langue sans image
    — impossible à choisir. Un bouton texte sert de repli."""
    source = _source("templates", "patient", "patient_front_page.html")
    assert 'flag-name' in source
    assert '{{ language.name }}' in source


def test_swap_langue_ne_duplique_pas_le_document():
    """`hx-get` sur la page complète avec innerHTML injectait tout le
    <html> DANS #main. `hx-select="#main"` + outerHTML remplace le bon
    fragment."""
    source = _source("templates", "patient", "patient_front_page.html")
    assert 'hx-select="#main"' in source
    assert 'hx-swap="outerHTML"' in source
    assert not re.search(r'hx-target="#main"[^>]*hx-swap="innerHTML"', source)


def test_attribut_lang_du_document_suit_le_clic():
    """Le swap HTMX ne touche pas <html> : le code choisi est porté par
    data-lang-code et appliqué par un écouteur délégué dans patients.js
    (pas de JavaScript inline — convention data-* du projet)."""
    source = _source("templates", "patient", "patient_front_page.html")
    assert 'data-lang-code="{{ language.code }}"' in source
    assert 'onclick=' not in source
    js = _source("static", "js", "patients.js")
    assert "document.documentElement.lang" in js
    assert "data-lang-code" in js


def test_css_focus_et_repli_texte():
    css = _source("static", "css", "patient.css")
    # Le focus clavier doit être visible sur les boutons-drapeaux.
    assert ".flag-button:focus-visible" in css
    assert ".flag-name" in css


def test_login_declare_sa_langue():
    """La page de connexion n'avait aucun attribut lang."""
    source = _source("templates", "security", "login.html")
    assert '<html lang="fr">' in source
