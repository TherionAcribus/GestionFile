"""Point 5.2 — chargement anticipé des relations lues par les gabarits.

Régression **statique** (pas de MySQL, pas d'import de l'app complète) : on
vérifie sur le source que les routes rendant une liste chargent en amont les
relations que le gabarit parcourt ligne par ligne (`joinedload`/`selectinload`),
au lieu de laisser SQLAlchemy émettre une requête par ligne (N+1). On vérifie
aussi que les regroupements de boutons parent/enfant ne relancent plus une
requête `Button.query.get()` par groupe dans une boucle, mais retrouvent le
parent dans un index en mémoire.

Complète `test_pagination_integration.py` (point 5.1) qui, lui, garantit que ces
mêmes routes paginent.
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

import pytest  # noqa: E402

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _body(rel, marker):
    """Corps d'une fonction repérée par `marker` (`def nom(`), jusqu'au prochain
    `def`/décorateur de même niveau d'indentation."""
    src = _read(rel)
    start = src.index(marker)
    tail = src[start:]
    # coupe au prochain 'def ' ou '@' en début de ligne après la 1re ligne
    m = re.search(r"\n(?:def |@)", tail[len(marker):])
    return tail if not m else tail[: len(marker) + m.start()]


# --------------------------------------------------------------------------
# patient -> activité / comptoir / langue
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rel, marker, needles", [
    # File patients (table admin) : le gabarit lit patient.activity et patient.counter.
    ("routes/admin_queue.py", "def display_queue_table",
     ["contains_eager(Patient.activity)", "joinedload(Patient.counter)"]),
    # Dashboard file : gabarit lit patient.activity.name.
    ("routes/admin_queue.py", "def dashboard_queue",
     ["joinedload(Patient.activity)"]),
    # File par comptoir : gabarit lit patient.activity.name + patient.language.code.
    ("routes/counter.py", "def patients_queue_for_counter",
     ["joinedload(Patient.activity)", "joinedload(Patient.language)"]),
])
def test_patient_relations_eager_loaded(rel, marker, needles):
    body = _body(rel, marker)
    for needle in needles:
        assert needle in body, f"{rel}:{marker} devrait charger {needle}"


# --------------------------------------------------------------------------
# utilisateur -> rôles
# --------------------------------------------------------------------------

def test_user_roles_eager_loaded():
    body = _body("routes/admin_security.py", "def display_security_table")
    assert "selectinload(User.roles)" in body


# --------------------------------------------------------------------------
# activité -> horaires
# --------------------------------------------------------------------------

@pytest.mark.parametrize("marker", [
    "def display_activity_table",
    "def display_activity_table_staff",
])
def test_activity_schedules_eager_loaded(marker):
    body = _body("routes/admin_activity.py", marker)
    assert "selectinload(Activity.schedules)" in body


# --------------------------------------------------------------------------
# comptoir -> staff (relation lue par les dashboards comptoir)
# --------------------------------------------------------------------------

def test_counter_staff_eager_loaded():
    body = _body("routes/admin_counter.py", "def dashboard_counter")
    assert "joinedload(Counter.staff)" in body


# --------------------------------------------------------------------------
# boutons parent/enfant : plus de requête par groupe dans une boucle
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "routes/admin_patient.py",
    "routes/admin_dashboard.py",
])
def test_button_grouping_uses_in_memory_index(rel):
    src = _read(rel)
    # Un index id -> bouton est construit à partir des boutons déjà chargés…
    assert "buttons_by_id" in src, f"{rel} devrait indexer les boutons en mémoire"
    # …et le parent est retrouvé dedans, pas via une requête dans la boucle.
    assert "buttons_by_id.get(parent_id)" in src
    assert "Button.query.get(parent_id)" not in src, (
        f"{rel} relance encore une requête par groupe (N+1)"
    )
