"""Point 2 (audit Admin) — Fiabilité des boutons « Enregistrer ».

Trois bugs corrigés :

1. **simple_button envoyait `variable` au lieu de `key`** à ``/admin/update_input``.
   La route lit ``request.values.get('key')`` : la valeur était donc toujours
   ``None`` → 400 « Unknown parameter ». La sauvegarde échouait silencieusement
   car le JS (``handleSimpleAfterRequest``) affichait « Enregistré ✓ » dans tous
   les cas.

2. **handleSimpleAfterRequest ne vérifiait jamais le succès/échec** de la
   requête HTMX : il mettait systématiquement à jour ``data-initial-value`` et
   désactivait le bouton, même après un 400/500.

3. **handleAfterRequest (variables CSS) avait le même défaut** : il ne
   consultait pas ``event.detail.successful`` ni le statut HTTP.

Les macros ``simple_input`` / ``simple_button`` utilisent désormais le même
schéma que ``input_field`` / ``button`` (``handleInputChangeConfig``,
``handleBeforeRequestConfig``, ``handleAfterRequestConfig``, envoi de ``key``,
``data-feedback-skip``, ``hx-swap="none"``). ``handleAfterRequest`` (CSS)
vérifie désormais le succès comme ``handleAfterRequestConfig``.

Vérifications statiques (``app.py`` exige MySQL et n'est pas importable ici) :
on lit le source des macros et du JS, comme ``test_config_change_feedback``.
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _macro_body(source, name):
    m = re.search(r"{% macro " + name + r"\(.*?%}(.*?){% endmacro %}",
                  source, re.DOTALL)
    assert m, f"macro {name} introuvable"
    return m.group(1)


# ---------------------------------------------------------------------------
# 1. simple_button : envoie `key` (et non `variable`) + handlers robustes
# ---------------------------------------------------------------------------

def test_simple_button_sends_key_not_variable():
    body = _macro_body(_read("templates/admin/macros.html"), "simple_button")
    # La route /admin/update_input lit `key` : c'est ce que le bouton doit envoyer.
    assert '"key": "{{ variable }}"' in body
    # Ne doit plus envoyer `source` ou `variable` comme paramètres séparés.
    assert '"variable":' not in body
    assert '"source":' not in body


def test_simple_button_uses_robust_handlers():
    body = _macro_body(_read("templates/admin/macros.html"), "simple_button")
    # Avant : désactive le bouton pendant la requête.
    assert "handleBeforeRequestConfig('{{ variable }}')" in body
    # Après : vérifie succès/échec via event.detail.successful + statut HTTP.
    assert "handleAfterRequestConfig(event, '{{ variable }}')" in body
    # Plus de handleSimpleAfterRequest (qui ne vérifiait pas le succès).
    assert "handleSimpleAfterRequest" not in body


def test_simple_button_has_feedback_skip_and_swap_none():
    body = _macro_body(_read("templates/admin/macros.html"), "simple_button")
    # data-feedback-skip : le retour se fait près du champ, pas via le toast global.
    assert "data-feedback-skip" in body
    # hx-swap="none" : la réponse n'est pas injectée aveuglément dans #invisible.
    assert 'hx-swap="none"' in body
    assert 'hx-target="#invisible"' not in body


def test_simple_button_has_result_div_with_aria_live():
    body = _macro_body(_read("templates/admin/macros.html"), "simple_button")
    # Zone de message près du champ, annoncée au lecteur d'écran.
    assert 'id="{{ variable }}_result"' in body
    assert 'aria-live="polite"' in body


# ---------------------------------------------------------------------------
# 2. simple_input : id = variable (plus source_variable) + handlers robustes
# ---------------------------------------------------------------------------

def test_simple_input_uses_variable_as_id():
    body = _macro_body(_read("templates/admin/macros.html"), "simple_input")
    # L'id doit être `variable` (la clé de config), pas `source_variable`.
    assert 'id="{{ variable }}"' in body
    assert 'id="{{ source }}_{{ variable }}"' not in body


def test_simple_input_uses_robust_handlers():
    body = _macro_body(_read("templates/admin/macros.html"), "simple_input")
    # handleInputChangeConfig : active/désactive le bouton selon la valeur initiale.
    assert "handleInputChangeConfig('{{ variable }}')" in body
    # handleKeyPressConfig : Entrée = sauvegarder (avec Shift+Enter pour saut de ligne).
    assert "handleKeyPressConfig(event, '{{ variable }}')" in body
    # Plus de handleSimpleInputChange / handleSimpleKeyPress.
    assert "handleSimpleInputChange" not in body
    assert "handleSimpleKeyPress" not in body


# ---------------------------------------------------------------------------
# 3. handleAfterRequest (CSS) : vérifie le succès/échec
# ---------------------------------------------------------------------------

def test_handle_after_request_checks_success():
    js = _read("static/js/admin_macros.js")
    body = re.search(
        r"function handleAfterRequest\(event, source, variable\)\s*{(.*?)\n}",
        js, re.DOTALL)
    assert body, "handleAfterRequest(event, source, variable) introuvable"
    body = body.group(1)
    # Doit consulter event.detail.successful et le statut HTTP.
    assert "detail.successful" in body
    assert "xhr.status" in body
    # Doit distinguer succès (désactive le bouton, met à jour initialValue)
    # et échec (garde le bouton actif, affiche l'erreur).
    assert "input.dataset.initialValue = input.value" in body
    assert "button.disabled = false" in body


def test_handle_before_request_disables_button():
    js = _read("static/js/admin_macros.js")
    body = re.search(
        r"function handleBeforeRequest\(source, variable\)\s*{(.*?)\n}",
        js, re.DOTALL)
    assert body, "handleBeforeRequest(source, variable) introuvable"
    body = body.group(1)
    assert "button.disabled = true" in body
    assert "Enregistrement" in body


# ---------------------------------------------------------------------------
# 4. css_unit_button : câble before/after + data-feedback-skip
# ---------------------------------------------------------------------------

def test_css_unit_button_wires_before_and_after_handlers():
    body = _macro_body(_read("templates/admin/macros.html"), "css_unit_button")
    # Avant : désactive le bouton pendant la requête.
    assert "handleBeforeRequest('{{ source }}', '{{ variable }}')" in body
    # Après : passe event pour vérifier succès/échec.
    assert "handleAfterRequest(event, '{{ source }}', '{{ variable }}')" in body
    # data-feedback-skip + hx-swap="none" (cohérent avec `button`).
    assert "data-feedback-skip" in body
    assert 'hx-swap="none"' in body
    assert 'hx-target="#invisible"' not in body


def test_css_unit_button_has_result_div_with_aria_live():
    body = _macro_body(_read("templates/admin/macros.html"), "css_unit_button")
    assert 'id="{{ source }}_{{ variable }}_result"' in body
    assert 'aria-live="polite"' in body
