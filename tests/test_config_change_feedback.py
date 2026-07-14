"""Régression du point 14 : retours de sauvegarde dans l'administration.

Objectif : la sauvegarde d'un champ de configuration doit renvoyer son résultat
DIRECTEMENT à l'auteur de la requête (statut HTTP + message dans le corps), afin
que le JavaScript (``handleAfterRequestConfig``) puisse :

- distinguer succès et échec via ``event.detail.successful`` et le statut HTTP ;
- ne mettre à jour la valeur initiale du champ qu'en cas de succès ;
- en cas d'erreur, garder le bouton actif et afficher le message près du champ ;
- désactiver le bouton pendant la requête ;

et sans diffuser le résultat par WebSocket à TOUS les administrateurs.

``app.py`` exige MySQL et n'est pas importable ici : on vérifie donc les sources
(comme les autres tests de régression statique de ce dépôt).
"""

import os
import re

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _route_body(source, route):
    m = re.search(r"def " + route + r"\(.*?\n(.*?)(?=\ndef |\n@app\.route)",
                  source, re.DOTALL)
    assert m, f"fonction {route} introuvable"
    return m.group(1)


def _func_body(source, func):
    m = re.search(r"def " + func + r"\(.*?\n(.*?)(?=\ndef )", source, re.DOTALL)
    assert m, f"fonction {func} introuvable"
    return m.group(1)


# ---------------------------------------------------------------------------
# Serveur : réponse directe au client, statut distinct, pas de diffusion WS
# ---------------------------------------------------------------------------

def test_config_change_response_exists_with_distinct_status():
    body = _func_body(_read("app.py"), "config_change_response")
    # Statut 400 en cas d'échec, 200 en cas de succès.
    assert "200 if success else 400" in body
    # Cette fonction ne diffuse PAS par WebSocket (contrairement à display_toast).
    assert "communikation" not in body


def test_update_input_uses_config_change_response_not_toast():
    body = _route_body(_read("app.py"), "update_input")
    # La route répond directement au client demandeur…
    assert "config_change_response(" in body
    # …et ne diffuse plus le résultat par WebSocket à tous les admins.
    assert "display_toast(" not in body
    assert "communikation" not in body


def test_update_input_failures_return_failure_status():
    body = _route_body(_read("app.py"), "update_input")
    # Les cas d'échec de validation renvoient bien success=False (→ HTTP 400).
    assert "config_change_response(success=False" in body
    # Et le cas nominal renvoie un succès.
    assert "config_change_response(success=True, message=\"Option mise à jour.\")" in body


def test_update_input_still_guards_restart_required():
    # Régression : ne pas casser la garde « redémarrage requis » (point 11).
    body = _route_body(_read("app.py"), "update_input")
    assert "spec.restart_required" in body
    assert "RESTART_REQUIRED_MESSAGE" in body


# ---------------------------------------------------------------------------
# Client (macros.html) : bouton + handlers
# ---------------------------------------------------------------------------

def _macro_body(source, name):
    m = re.search(r"{% macro " + name + r"\(.*?%}(.*?){% endmacro %}",
                  source, re.DOTALL)
    assert m, f"macro {name} introuvable"
    return m.group(1)


def test_button_macro_wires_before_and_after_handlers():
    body = _macro_body(_read("templates/admin/macros.html"), "button")
    # La réponse n'est plus injectée aveuglément dans #invisible.
    assert 'hx-swap="none"' in body
    assert 'hx-target="#invisible"' not in body
    # Bouton désactivé pendant la requête + traitement du résultat.
    assert "handleBeforeRequestConfig('{{ key }}')" in body
    assert "handleAfterRequestConfig(event, '{{ key }}')" in body
    # Zone de message près du champ.
    assert 'id="{{ key }}_result"' in body


def test_before_request_handler_disables_button():
    js = _read("templates/admin/macros.html")
    body = re.search(r"function handleBeforeRequestConfig\(variable\)\s*{(.*?)\n}",
                     js, re.DOTALL).group(1)
    assert "button.disabled = true" in body


def test_after_request_handler_checks_success_and_status():
    js = _read("templates/admin/macros.html")
    # Corps de la fonction, borné à sa dernière accolade avant </script>.
    body = re.search(
        r"function handleAfterRequestConfig\(event, variable\)\s*{(.*?)\n}\n\n</script>",
        js, re.DOTALL).group(1)
    # Vérifie le succès htmx ET le statut HTTP 2xx.
    assert "detail.successful" in body
    assert "status >= 200 && status < 300" in body

    # La valeur initiale n'est mise à jour que dans la branche succès.
    ok_branch = re.search(r"if \(ok\) {(.*?)} else {", body, re.DOTALL).group(1)
    err_branch = re.search(r"} else {(.*)", body, re.DOTALL).group(1)
    assert "input.dataset.initialValue = input.value" in ok_branch
    assert "input.dataset.initialValue" not in err_branch

    # En cas d'erreur : bouton réactivé + message d'erreur affiché.
    assert "button.disabled = false" in err_branch
    assert "text-danger" in err_branch
