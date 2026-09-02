"""Point 9.3 — le code serveur journalise, il n'imprime pas.

Trois garde-fous statiques (aucune base, aucun réseau) :

1. plus aucun `print()` dans le code de production — les traces doivent passer
   par `app.logger` / `current_app.logger`, seuls capables de respecter le
   niveau configuré, d'horodater et d'atteindre les journaux du conteneur ;
2. aucun bloc `except` ne se contente d'un `print` : une erreur diagnostiquée
   sur stdout puis oubliée est une erreur invisible en production ;
3. aucune séquence d'échappement invalide (`"\\}"`, `"\\["`…), qui deviendra une
   erreur de syntaxe dans une version future de Python.
"""

import ast
import io
import os
import warnings

import pytest

_RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

# Répertoires hors périmètre : dépendances, tests, migrations générées.
_IGNORES = {".venv", "venv", "env", "tests", "migrations", "__pycache__",
            ".git", "static", "node_modules", "instance"}


def _fichiers_source():
    for base, dirs, fichiers in os.walk(_RACINE):
        dirs[:] = [d for d in dirs if d not in _IGNORES]
        for nom in sorted(fichiers):
            if nom.endswith(".py"):
                chemin = os.path.join(base, nom)
                yield os.path.relpath(chemin, _RACINE).replace(os.sep, "/"), chemin


def _arbre(chemin):
    with warnings.catch_warnings(record=True) as captures:
        warnings.simplefilter("always")
        source = io.open(chemin, encoding="utf-8").read()
        arbre = ast.parse(source)
    return arbre, captures


def _est_print(noeud):
    return (isinstance(noeud, ast.Call) and isinstance(noeud.func, ast.Name)
            and noeud.func.id == "print")


@pytest.mark.parametrize("rel,chemin", list(_fichiers_source()))
def test_aucun_print_dans_le_code_serveur(rel, chemin):
    arbre, _ = _arbre(chemin)
    trouves = [n.lineno for n in ast.walk(arbre) if _est_print(n)]
    assert trouves == [], (
        f"{rel} : print() en ligne(s) {trouves}. Utilisez app.logger.debug/info/"
        f"warning/error (ou current_app.logger) : un print n'est ni horodaté, ni "
        f"filtrable par niveau, et se perd hors d'un terminal."
    )


@pytest.mark.parametrize("rel,chemin", list(_fichiers_source()))
def test_aucun_except_ne_se_contente_dune_trace_stdout(rel, chemin):
    arbre, _ = _arbre(chemin)
    coupables = []
    for noeud in ast.walk(arbre):
        if not isinstance(noeud, ast.ExceptHandler):
            continue
        contenu = list(ast.walk(noeud))
        a_print = any(_est_print(n) for n in contenu)
        a_log = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in {"debug", "info", "warning", "error", "exception", "critical"}
            for n in contenu
        )
        a_raise = any(isinstance(n, ast.Raise) for n in contenu)
        if a_print and not a_log and not a_raise:
            coupables.append(noeud.lineno)
    assert coupables == [], (
        f"{rel} : bloc(s) except en ligne(s) {coupables} qui impriment l'erreur "
        f"sans la journaliser ni la relancer — elle disparaît silencieusement."
    )


@pytest.mark.parametrize("rel,chemin", list(_fichiers_source()))
def test_aucune_sequence_dechappement_invalide(rel, chemin):
    _, captures = _arbre(chemin)
    invalides = [
        f"ligne {c.lineno} : {c.message}"
        for c in captures if "escape sequence" in str(c.message)
    ]
    assert invalides == [], (
        f"{rel} : séquence(s) d'échappement invalide(s) — {invalides}. "
        f"Utilisez une chaîne brute (r\"...\") : ces séquences deviendront des "
        f"erreurs de syntaxe dans une version future de Python."
    )
