"""Point « CSP stricte » — les gabarits et le JS ne doivent plus contenir de
mécanismes incompatibles avec ``script-src 'self'``.

La CSP de ``app.py`` interdit les scripts inline et ``eval`` (pas de
``'unsafe-inline'`` ni ``'unsafe-eval'``). Sont donc proscrits :

- les attributs de gestion d'évènements inline (``onclick``, ``onchange``,
  ``oninput``, ``onkeydown``, ``onsubmit``, etc.) ;
- ``hx-vals="js:..."`` / ``hx-vals="javascript:..."`` et ``hx-vars="js:..."``
  — htmx les compile avec ``Function(...)`` ;
- ``hx-on`` / ``hx-on:`` / ``hx-on::`` — compilés avec ``Function(...)`` ;
- les filtres d'évènements ``hx-trigger="...[<expr>]"`` — idem ;
- les URL ``javascript:`` ;
- les handlers inline générés en chaîne depuis le JS
  (``innerHTML = "... onclick=..."``).

Le remplacement repose sur ``static/js/htmx_params.js`` (attributs
``data-param-*`` résolus au ``htmx:configRequest``), des écouteurs délégués et
``hx-include``.

Vérifications statiques : on lit le source, comme les autres tests de
régression statique de ce dépôt.
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_TEMPLATES = os.path.join(_SERVEUR, "templates")
_JS = os.path.join(_SERVEUR, "static", "js")


def _gabarits():
    out = []
    for base, _dirs, files in os.walk(_TEMPLATES):
        for f in sorted(files):
            if f.endswith(".html"):
                out.append(
                    os.path.relpath(os.path.join(base, f), _TEMPLATES)
                    .replace(os.sep, "/")
                )
    return sorted(out)


def _fichiers_js():
    out = []
    for base, _dirs, files in os.walk(_JS):
        for f in sorted(files):
            if f.endswith(".js") and "libs" not in os.path.relpath(
                    base, _JS).split(os.sep):
                out.append(
                    os.path.relpath(os.path.join(base, f), _JS)
                    .replace(os.sep, "/")
                )
    return sorted(out)


def _lire(rel, root=_TEMPLATES):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


def _code_utile(source):
    """Source sans les commentaires Jinja ``{#...#}`` ni HTML ``<!--...-->``."""
    source = re.sub(r"\{#.*?#\}", "", source, flags=re.DOTALL)
    source = re.sub(r"<!--.*?-->", "", source, flags=re.DOTALL)
    return source


# ---------------------------------------------------------------------------
# Gabarits : aucun mécanisme évalué / inline
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("gabarit", _gabarits())
def test_gabarit_sans_attribut_inline(gabarit):
    """Aucun attribut on<event>= dans les gabarits (hors commentaires)."""
    code = _code_utile(_lire(gabarit))
    fautifs = [m.group(0) for m in re.finditer(r"\son[a-zA-Z]+\s*=", code)]
    assert not fautifs, (
        f"{gabarit} contient des handlers inline : {fautifs}"
    )


@pytest.mark.parametrize("gabarit", _gabarits())
def test_gabarit_sans_hx_vals_js(gabarit):
    """hx-vals ne doit plus utiliser le préfixe js:/javascript: (Function())."""
    code = _code_utile(_lire(gabarit))
    fautifs = re.findall(
        r"""hx-vals\s*=\s*["']\s*(?:js|javascript)\s*:[^"']*["']""", code)
    assert not fautifs, (
        f"{gabarit} contient des hx-vals évalués : {fautifs}"
    )


@pytest.mark.parametrize("gabarit", _gabarits())
def test_gabarit_sans_hx_on(gabarit):
    """hx-on (toutes syntaxes) compile le handler via Function() : proscrit."""
    code = _code_utile(_lire(gabarit))
    fautifs = re.findall(r"\shx-on[:\s=][^\s>]*", code)
    assert not fautifs, (
        f"{gabarit} contient des attributs hx-on : {fautifs}"
    )


@pytest.mark.parametrize("gabarit", _gabarits())
def test_gabarit_sans_hx_vars(gabarit):
    """hx-vars est l'ancêtre évalué de hx-vals : proscrit."""
    code = _code_utile(_lire(gabarit))
    fautifs = re.findall(r"\shx-vars\s*=", code)
    assert not fautifs, f"{gabarit} contient hx-vars"


@pytest.mark.parametrize("gabarit", _gabarits())
def test_gabarit_sans_filtre_hx_trigger(gabarit):
    """Les filtres hx-trigger="evt[<expr>]" sont compilés via Function().

    Pour « Entrée » utiliser ``data-enter-trigger`` (voir htmx_params.js)
    ou un écouteur délégué.
    """
    code = _code_utile(_lire(gabarit))
    fautifs = re.findall(r"""hx-trigger\s*=\s*["'][^"']*\[[^"']*["']""", code)
    assert not fautifs, (
        f"{gabarit} contient des filtres hx-trigger évalués : {fautifs}"
    )


@pytest.mark.parametrize("gabarit", _gabarits())
def test_gabarit_sans_url_javascript(gabarit):
    """Les URL javascript: sont bloquées par script-src 'self'."""
    code = _code_utile(_lire(gabarit))
    fautifs = re.findall(r"""["'\s]javascript\s*:""", code)
    assert not fautifs, f"{gabarit} contient des URL javascript:"


# ---------------------------------------------------------------------------
# JS statique : aucun handler inline généré en chaîne
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fichier", _fichiers_js())
def test_js_sans_handler_inline_genere(fichier):
    """Le JS ne doit pas produire de HTML avec attributs on<event>=."""
    source = _lire(fichier, root=_JS)
    fautifs = []
    for i, ligne in enumerate(source.splitlines(), 1):
        nu = ligne.strip()
        if nu.startswith("//") or nu.startswith("*"):
            continue
        for m in re.finditer(r"""\son[a-zA-Z]+\s*=\s*["']""", ligne):
            fautifs.append(f"ligne {i}: {nu}")
    assert not fautifs, (
        f"{fichier} génère des handlers inline :\n" + "\n".join(fautifs)
    )


@pytest.mark.parametrize("fichier", _fichiers_js())
def test_js_sans_eval_ni_function_dynamique(fichier):
    """Ni eval() ni new Function() dans le JS applicatif (CSP sans unsafe-eval)."""
    source = _lire(fichier, root=_JS)
    fautifs = []
    for i, ligne in enumerate(source.splitlines(), 1):
        nu = ligne.strip()
        if nu.startswith("//") or nu.startswith("*"):
            continue
        if re.search(r"\beval\s*\(|\bnew\s+Function\s*\(", ligne):
            fautifs.append(f"ligne {i}: {nu}")
    assert not fautifs, (
        f"{fichier} utilise une évaluation dynamique :\n" + "\n".join(fautifs)
    )


# ---------------------------------------------------------------------------
# Plomberie : htmx_params.js chargé partout où des fragments HTMX vivent
# ---------------------------------------------------------------------------

def test_htmx_params_charge_dans_base_admin():
    source = _lire("admin/base.html")
    assert "js/htmx_params.js" in source


def test_htmx_params_charge_dans_counter():
    source = _lire("counter/counter.html")
    assert "js/htmx_params.js" in source


def test_htmx_params_desactive_allow_eval():
    """Défense en profondeur : htmx.config.allowEval = false coupe toutes les
    voies Function() de htmx, même en cas de régression dans un gabarit."""
    source = _lire("htmx_params.js", root=_JS)
    assert "allowEval" in source
    assert "htmx:configRequest" in source
    assert "data-param-" in source


def test_htmx_params_registre_collecteurs():
    """Le registre HX_PARAM_COLLECTORS doit être exposé (utilisé par les
    collecteurs nommés data-params-fn, ex. rolePermissions / couleurs)."""
    source = _lire("htmx_params.js", root=_JS)
    assert "HX_PARAM_COLLECTORS" in source
    assert "rolePermissions" in source
