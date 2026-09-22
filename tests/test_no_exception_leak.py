"""Régression : aucun détail d'exception ne doit partir vers le navigateur.

Point sécurité « fuites de détails techniques » : les messages d'exception
(``str(e)``, ``f"...{e}"``…) appartiennent aux journaux applicatifs, jamais aux
réponses HTTP — ils peuvent contenir des chemins internes, des DSN ou des
fragments de requêtes SQL.

Ce test balaie les routes et les helpers qui fabriquent des réponses
(``display_toast``, ``jsonify``, ``return``, ``flash``, ``render_template``) et
interdit toute interpolation d'exception dans ces expressions. Le détail doit
rester côté serveur via ``app.logger.exception(...)``.
"""

import os
import re

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

#: Fichiers dont les expressions fabriquent des réponses HTTP ou des toasts.
_SCANNED = ["routes", "bdd.py", "communication.py"]

#: Une exception transite dans la ligne (str(e), repr(e), {e}, {exc}…).
_EXCEPTION_LEAK = re.compile(r"str\(e\)|repr\(e\)|\{e\}|\{exc\}|str\(exc\)")

#: Marqueurs d'une expression qui part vers le client.
_RESPONSE_MARKER = re.compile(
    r"display_toast\(|jsonify\(|return |flash\(|render_template"
)

#: Cas légitimes : messages de validation contrôlés (BackupValidationError est
#: conçu pour l'affichage — ses messages sont produits par nos validateurs,
#: jamais par une exception technique de bas niveau).
def _is_allowed(rel, line):
    return rel == "routes/admin_backup.py" and "return None, str(e)" in line


def _iter_response_lines():
    for entry in _SCANNED:
        path = os.path.join(_SERVEUR, entry)
        if os.path.isdir(path):
            for name in sorted(os.listdir(path)):
                if name.endswith(".py"):
                    yield from _iter_file(os.path.join(path, name),
                                          f"{entry}/{name}")
        else:
            yield from _iter_file(path, entry)


def _iter_file(abspath, rel):
    with open(abspath, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if line.startswith("#"):
                continue
            yield rel, lineno, line


def test_no_exception_details_in_responses():
    violations = []
    for rel, lineno, line in _iter_response_lines():
        if not (_EXCEPTION_LEAK.search(line) and _RESPONSE_MARKER.search(line)):
            continue
        # Les journaux sont la destination légitime du détail.
        if "logger" in line or "logging" in line:
            continue
        if _is_allowed(rel, line):
            continue
        violations.append(f"{rel}:{lineno}: {line}")
    assert not violations, (
        "Détails d'exception renvoyés au client :\n" + "\n".join(violations)
    )
