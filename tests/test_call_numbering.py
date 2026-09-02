"""Numérotation « simple » des numéros d'appel (`call_numbering` + `engine`).

Bug corrigé : `Patient.call_number` est une colonne texte (`db.String(10)`),
donc `last_patient_today.call_number + 1` levait `TypeError: can only
concatenate str (not "int") to str` — dès le **deuxième** patient de la journée
quand `NUMBERING_BY_ACTIVITY` est désactivé.

Deux niveaux :
- cœur pur (`next_simple_call_number`), sans base ;
- chemin réel (`engine.get_next_call_number_simple`) sur SQLite en mémoire,
  pour que la régression ne puisse pas revenir par la requête.
"""

from datetime import datetime, timedelta

import pytest
from flask import Flask

from call_numbering import next_simple_call_number


# --- cœur pur ---------------------------------------------------------------

def test_aucun_patient_aujourdhui_commence_a_1():
    assert next_simple_call_number(None) == "1"


def test_increment_sur_une_chaine():
    # Le cas qui levait TypeError : la base rend bien une chaîne.
    assert next_simple_call_number("1") == "2"
    assert next_simple_call_number("9") == "10"
    assert next_simple_call_number("41") == "42"


def test_resultat_toujours_une_chaine():
    resultat = next_simple_call_number("7")
    assert isinstance(resultat, str)
    assert isinstance(next_simple_call_number(None), str)


def test_increment_accepte_un_entier():
    # Tolérance : certains back-ends peuvent rendre un entier.
    assert next_simple_call_number(3) == "4"


def test_espaces_ignores():
    assert next_simple_call_number(" 12 ") == "13"


def test_numero_par_activite_repart_a_1():
    # "A-3" n'est pas numérique : limitation connue, mais pas de crash.
    assert next_simple_call_number("A-3") == "1"


def test_valeurs_non_numeriques_repartent_a_1():
    for valeur in ("", "   ", "abc", "1.5", "-2", "٣", "²"):
        assert next_simple_call_number(valeur) == "1", valeur


# --- chemin réel (SQLite en mémoire) ----------------------------------------

@pytest.fixture
def ctx():
    from models import db, Activity, Patient

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    app.config["TESTING"] = True
    db.init_app(app)

    with app.app_context():
        db.create_all()
        activity = Activity(name="Ordonnance", letter="A")
        db.session.add(activity)
        db.session.commit()
        yield app, db, activity, Patient
        db.session.remove()
        db.drop_all()


def _ajouter_patient(db, Patient, activity, call_number, timestamp=None):
    patient = Patient(
        call_number=call_number,
        activity_id=activity.id,
        timestamp=timestamp or datetime.now(),
        status="standing",
    )
    db.session.add(patient)
    db.session.commit()
    return patient


def test_file_vide_donne_1(ctx):
    app, db, activity, Patient = ctx
    from python.engine import get_next_call_number_simple

    assert get_next_call_number_simple() == "1"


def test_suite_de_numeros_sans_typeerror(ctx):
    app, db, activity, Patient = ctx
    from python.engine import get_next_call_number_simple

    numeros = []
    for _ in range(3):
        numero = get_next_call_number_simple()
        numeros.append(numero)
        _ajouter_patient(db, Patient, activity, numero)

    assert numeros == ["1", "2", "3"]


def test_patients_des_jours_precedents_ignores(ctx):
    app, db, activity, Patient = ctx
    from python.engine import get_next_call_number_simple

    _ajouter_patient(db, Patient, activity, "57", timestamp=datetime.now() - timedelta(days=1))
    assert get_next_call_number_simple() == "1"


def test_dernier_numero_par_activite_repart_a_1(ctx):
    app, db, activity, Patient = ctx
    from python.engine import get_next_call_number_simple

    _ajouter_patient(db, Patient, activity, "A-3")
    assert get_next_call_number_simple() == "1"
