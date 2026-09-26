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


# --- compteur persistant (attribution atomique, pas de réattribution) ------

def test_suppression_ne_reattribue_pas_le_numero(ctx):
    """Le compteur survit aux suppressions : retirer le dernier patient de
    la file ne remet plus son numéro en circulation (avant : « 3 » supprimé
    -> prochain patient « 3 » en double)."""
    app, db, activity, Patient = ctx
    from python.engine import get_next_call_number_simple

    # Flux réel : chaque numéro attribué est consommé par le compteur.
    for _ in range(3):
        _ajouter_patient(db, Patient, activity, get_next_call_number_simple())
    db.session.delete(Patient.query.filter_by(call_number="3").one())
    db.session.commit()

    # L'ancien calcul (« dernier patient + 1 ») rendait « 3 » à nouveau.
    assert get_next_call_number_simple() == "4"


def test_amorçage_inclut_les_numeros_archives(ctx):
    """Bootstrap : un numéro déjà purgé dans l'historique a quand même été
    consommé — l'amorçage du compteur le prend en compte."""
    app, db, activity, Patient = ctx
    from models import PatientHistory
    from python.engine import get_next_call_number_simple

    db.session.add(PatientHistory(
        call_number="9", activity_id=activity.id,
        timestamp=datetime.now(), day_of_week="Mon", status="done"))
    db.session.commit()

    assert get_next_call_number_simple() == "10"


def test_amorçage_sur_les_numeros_existants(ctx):
    """Journée déjà entamée avant la ligne de compteur : le premier tirage
    s'amorce sur le plus grand numéro attribué, pas sur 1."""
    app, db, activity, Patient = ctx
    from python.engine import get_next_call_number_simple

    _ajouter_patient(db, Patient, activity, "5")
    _ajouter_patient(db, Patient, activity, "7")
    assert get_next_call_number_simple() == "8"


def test_peek_ne_consomme_pas(ctx):
    """L'aperçu « futur patient » n'attribue rien : deux affichages donnent
    la même prévision et l'attribution suivante rend ce numéro."""
    app, db, activity, Patient = ctx
    from python.engine import peek_next_call_number, get_next_call_number_simple

    assert peek_next_call_number(activity) == "1"
    assert peek_next_call_number(activity) == "1"
    assert get_next_call_number_simple() == "1"


def test_mode_categories_series_par_lettre(ctx):
    """Numérotation par activité : chaque lettre a sa série, indépendante
    des autres, avec le format 'L-n'."""
    app, db, activity, Patient = ctx
    app.config["NUMBERING_BY_ACTIVITY"] = True
    from models import Activity as _Activity
    from python.engine import get_next_category_number

    autre = _Activity(name="Certificat", letter="B")
    db.session.add(autre)
    db.session.commit()

    assert get_next_category_number(activity) == "A-1"
    assert get_next_category_number(activity) == "A-2"
    assert get_next_category_number(autre) == "B-1"


def test_categories_ne_reattribue_pas_apres_suppression(ctx):
    """Suppression du plus grand numéro de la lettre : le compteur ne
    redescend pas."""
    app, db, activity, Patient = ctx
    app.config["NUMBERING_BY_ACTIVITY"] = True
    from python.engine import get_next_category_number

    assert get_next_category_number(activity) == "A-1"
    assert get_next_category_number(activity) == "A-2"
    patient = _ajouter_patient(db, Patient, activity, "A-2")
    db.session.delete(patient)
    db.session.commit()

    assert get_next_category_number(activity) == "A-3"


def test_retour_en_simple_continue_la_serie(ctx):
    """simple -> par activité -> simple dans la journée : le compteur
    « simple » survit au passage, au lieu de repartir à 1 (ancienne
    limitation connue)."""
    app, db, activity, Patient = ctx
    from python.engine import get_next_call_number_simple, get_next_category_number

    assert get_next_call_number_simple() == "1"
    app.config["NUMBERING_BY_ACTIVITY"] = True
    assert get_next_category_number(activity) == "A-1"
    app.config["NUMBERING_BY_ACTIVITY"] = False
    assert get_next_call_number_simple() == "2"
