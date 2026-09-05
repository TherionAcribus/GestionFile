"""Point 7 (audit Admin) — Test de la validation de `days` (fonction pure).

La fonction ``_validate_days`` dans ``routes/admin_data.py`` est la garde
critique contre les valeurs aberrantes du paramètre ``days`` (archivage,
suppression de stats). Avant le point 4, une valeur négative déplaçait la date
de cutoff dans le futur (élargissant le périmètre de suppression) et une valeur
non entière générait un 500 avec fuite d'information technique.

Ce test valide le comportement de ``_validate_days`` **sans MySQL ni Flask** :
la fonction est extraite du source par ``exec`` (le module ``admin_data.py``
importe ``models`` qui exige MySQL, donc on ne peut pas l'importer directement).
C'est la même technique que ``test_permissions_centralized`` pour tester du
code qui vit dans des modules non importables.
"""

import os
import re
import textwrap

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _extract_validate_days():
    """Extrait _validate_days et ses constantes depuis le source de admin_data.py.

    On ne peut pas ``from routes.admin_data import _validate_days`` car le
    module importe ``models`` (exige MySQL). On lit donc le source, on extrait
    la fonction et les constantes _DAYS_MIN / _DAYS_MAX, et on les évalue dans
    un namespace isolé.
    """
    with open(os.path.join(_SERVEUR, "routes", "admin_data.py"),
              encoding="utf-8") as fh:
        source = fh.read()

    # Extraire les constantes
    ns = {}
    for m in re.finditer(r"^(_DAYS_\w+)\s*=\s*(\d+)", source, re.MULTILINE):
        ns[m.group(1)] = int(m.group(2))

    # Extraire la fonction _validate_days : on stoppe au prochain `def` ou `@`
    # au niveau 0 (colonne 0), pour ne pas capturer les décorateurs de la
    # fonction suivante.
    m = re.search(
        r"(def _validate_days\(raw\):.*?)(?=^(?:def |@|\Z))",
        source, re.DOTALL | re.MULTILINE)
    assert m, "_validate_days introuvable dans routes/admin_data.py"
    func_src = textwrap.dedent(m.group(1))

    # Évaluer dans le namespace qui contient déjà les constantes
    exec(func_src, ns)
    return ns["_validate_days"], ns["_DAYS_MIN"], ns["_DAYS_MAX"]


# Fixture : la fonction extraite, disponible pour tous les tests
@pytest.fixture(scope="module")
def validate():
    func, dmin, dmax = _extract_validate_days()
    return func, dmin, dmax


# ---------------------------------------------------------------------------
# Cas valides
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1", 1),       # minimum
    ("30", 30),     # valeur typique
    ("365", 365),   # valeur par défaut
    ("3650", 3650), # maximum (10 ans)
])
def test_validate_days_accepts_valid(raw, expected, validate):
    func, dmin, dmax = validate
    days, error = func(raw)
    assert error is None
    assert days == expected


# ---------------------------------------------------------------------------
# Cas invalides : valeurs manquantes / vides
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [None, ""])
def test_validate_days_rejects_empty(raw, validate):
    func, dmin, dmax = validate
    days, error = func(raw)
    assert days is None
    assert error is not None
    assert "manquant" in error.lower()


def test_validate_days_rejects_whitespace(validate):
    """Une valeur de spaces uniquement n'est pas vide mais n'est pas un entier."""
    func, dmin, dmax = validate
    days, error = func("   ")
    assert days is None
    assert error is not None
    assert "entier" in error.lower()


# ---------------------------------------------------------------------------
# Cas invalides : non-entiers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["abc", "1.5", "3,14", "trois", "1e10", "NaN"])
def test_validate_days_rejects_non_integer(raw, validate):
    func, dmin, dmax = validate
    days, error = func(raw)
    assert days is None
    assert error is not None
    assert "entier" in error.lower()


# ---------------------------------------------------------------------------
# Cas invalides : hors bornes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["0", "-1", "-365"])
def test_validate_days_rejects_zero_and_negative(raw, validate):
    """Une valeur négative ou zéro ne doit pas produire un cutoff dans le
    futur ou aujourd'hui (élargirait le périmètre de suppression)."""
    func, dmin, dmax = validate
    days, error = func(raw)
    assert days is None
    assert error is not None
    # Le message doit mentionner le minimum attendu.
    assert str(dmin) in error


@pytest.mark.parametrize("raw", ["3651", "9999", "100000"])
def test_validate_days_rejects_above_max(raw, validate):
    func, dmin, dmax = validate
    days, error = func(raw)
    assert days is None
    assert error is not None
    assert str(dmax) in error


# ---------------------------------------------------------------------------
# Cas limites : types non-string (robustesse)
# ---------------------------------------------------------------------------

def test_validate_days_handles_int_input(validate):
    """Un int passé directement doit aussi fonctionner (int(int) == int)."""
    func, dmin, dmax = validate
    days, error = func(30)
    assert error is None
    assert days == 30


def test_validate_days_handles_none_input(validate):
    """None doit être traité comme manquant, pas lever une exception."""
    func, dmin, dmax = validate
    days, error = func(None)
    assert days is None
    assert error is not None


# ---------------------------------------------------------------------------
# Le message d'erreur est sûr (aucun détail technique)
# ---------------------------------------------------------------------------

def test_error_messages_are_safe(validate):
    """Les messages d'erreur ne doivent pas contenir de trace technique
    (nom d'exception, chemin de fichier, etc.)."""
    func, dmin, dmax = validate
    for raw in [None, "abc", "-1", "99999"]:
        _, error = func(raw)
        assert error is not None
        # Pas de mention d'exception Python.
        assert "ValueError" not in error
        assert "TypeError" not in error
        assert "Traceback" not in error
        assert ".py" not in error
