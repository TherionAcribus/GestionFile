"""Régression : les routes d'actions modificatrices sont POST-only (point 14).

Une action qui modifie l'état ne doit plus être accessible en GET. Avec
``methods=['POST']``, Flask renvoie automatiquement 405 (Method Not Allowed) sur
un GET. On vérifie statiquement la déclaration des routes (le vrai app.py exige
MySQL et n'est pas importable ici).
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# (fichier, chemin de route) attendus en POST uniquement.
POST_ONLY_ROUTES = [
    ("routes/counter.py", "/api/counter/put_standing_list/<int:patient_id>/<int:activity_id>"),
    ("routes/counter.py", "/api/counter/put_standing_list/<int:patient_id>"),
    ("routes/counter.py", "/api/counter/validate_patient/<int:patient_id>"),
    ("routes/counter.py", "/api/counter/delete_patient/<int:patient_id>"),
    ("app.py", "/call_specific_patient/<int:counter_id>/<int:patient_id>"),
    ("app.py", "/validate_patient/<int:counter_id>/<int:patient_id>"),
    ("app.py", "/validate_and_call_next/<int:counter_id>"),
    ("app.py", "/pause_patient/<int:counter_id>/<int:patient_id>"),
]


def _find_route_decorator(source, path):
    # Cherche @<bp>.route('<path>', methods=[...]) en échappant le chemin.
    pattern = re.compile(
        r"@\w+\.route\(\s*['\"]" + re.escape(path) + r"['\"]\s*,\s*methods\s*=\s*(\[[^\]]*\])",
    )
    m = pattern.search(source)
    return m.group(1) if m else None


@pytest.mark.parametrize("rel,path", POST_ONLY_ROUTES)
def test_route_is_post_only(rel, path):
    methods = _find_route_decorator(_read(rel), path)
    assert methods is not None, f"Route {path} introuvable dans {rel}"
    assert "POST" in methods, f"{path} doit accepter POST"
    assert "GET" not in methods, f"{path} ne doit plus accepter GET (405 attendu) : {methods}"
