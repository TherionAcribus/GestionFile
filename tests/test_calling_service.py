"""Point 9.4 — couche service « appel patient ».

Tests d'intégration réels (pas seulement statiques) : les **vrais** modèles
SQLAlchemy sont créés sur une base SQLite en mémoire, et les émissions temps réel
sont capturées. Aucun MySQL, aucun réseau, aucun serveur.

Ce que l'on verrouille :

1. la réclamation atomique partagée : deux comptoirs sur le même patient, un seul
   gagne (le bug historique était que les deux « décrochaient » le patient) ;
2. ``announce_call`` émet exactement les messages attendus — c'est le bloc qui
   existait en trois copies divergentes ;
3. ``run_auto_calling`` ne plante plus quand aucun patient ne convient au
   comptoir (l'ancienne version faisait ``patient.id`` sur la chaîne d'erreur) ;
4. l'ordre des comptoirs suit ``COUNTER_ORDER`` ;
5. l'appel automatique lit ``Counter.auto_calling`` en base et non une liste en
   mémoire propre au processus.
"""

import os

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, Counter, Language, Patient, Pharmacist


# --- Infrastructure de test -------------------------------------------------

@pytest.fixture
def application(monkeypatch):
    """Mini-application Flask sur SQLite, avec les vrais modèles."""
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
        ANNOUNCE_CALL_TEXT="Patient {N} au comptoir {C}",
        COUNTER_ORDER="order",
        # L'algorithme de priorite est hors sujet ici : on veut verifier le flux
        # d'appel, pas les regles de priorite (couvertes ailleurs).
        ALGO_IS_ACTIVATED=False,
        ALGO_OVERTAKEN_LIMIT=10,
        PHARMACY_NAME="Pharmacie de test",
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def messages(monkeypatch):
    """Capture les émissions temps réel du service et de ses dépendances."""
    captures = []

    def faux_communikation(stream, data=None, flag=None, event="update", client_id=None):
        captures.append((stream, event, data))

    import communication
    import services.calling_service as service
    import python.engine as engine

    for module in (service, engine):
        monkeypatch.setattr(module, "communikation", faux_communikation, raising=False)
    monkeypatch.setattr(communication, "communikation", faux_communikation, raising=False)
    monkeypatch.setattr(service, "notify_patient_phone", lambda *a, **k: None)
    monkeypatch.setattr(service, "send_app_notification", lambda *a, **k: None)
    monkeypatch.setattr(engine, "notify_patient_phone", lambda *a, **k: None)
    # La génération audio part dans un thread et tape un service externe.
    monkeypatch.setattr(service, "trigger_async_audio_calling", lambda *a, **k: None)
    monkeypatch.setattr(engine, "trigger_async_audio_calling", lambda *a, **k: None)
    return captures


def _jeu_de_donnees(nb_patients=1, nb_comptoirs=1, auto=False, avec_staff=True):
    """Jeu de donnees minimal mais REALISTE.

    Un membre d'equipe est rattache a chaque comptoir par defaut : sans lui,
    `replace_balise_announces` part dans sa branche degradee (le texte d'annonce
    devient "Erreur"), ce qui masquerait ce que l'on veut verifier.
    """
    langue = Language(code="fr", name="Français", translation="Français")
    activite = Activity(name="Ordonnance", letter="O")
    db.session.add_all([langue, activite])
    db.session.commit()

    comptoirs = []
    for i in range(nb_comptoirs):
        c = Counter(name=f"Comptoir {i + 1}", sort_order=i + 1,
                    is_active=False, auto_calling=auto)
        if avec_staff:
            # `algo_choice_next_patient` filtre les patients sur
            # counter.staff.activities : sans cette association, aucun patient
            # n'est jamais eligible.
            membre = Pharmacist(name=f"Membre {i + 1}", initials=f"M{i + 1}")
            membre.activities.append(activite)
            db.session.add(membre)
            c.staff = membre
        db.session.add(c)
        comptoirs.append(c)

    patients = []
    for i in range(nb_patients):
        p = Patient(call_number=100 + i, status="standing",
                    activity_id=activite.id, language_id=langue.id)
        db.session.add(p)
        patients.append(p)

    db.session.commit()
    return comptoirs, patients, activite


# --- 1. Réclamation atomique partagée ---------------------------------------

def test_un_seul_comptoir_decroche_un_patient(application, messages):
    from python.engine import claim_patient

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=2)
    patient = patients[0]

    premier = claim_patient(patient.id, comptoirs[0].id)
    second = claim_patient(patient.id, comptoirs[1].id)

    assert premier is True, "le premier comptoir doit decrocher le patient"
    assert second is False, "le second ne doit PAS decrocher le meme patient"

    rafraichi = db.session.get(Patient, patient.id)
    assert rafraichi.status == "calling"
    assert rafraichi.counter_id == comptoirs[0].id, (
        "le counter_id du gagnant a ete ecrase par le perdant"
    )


def test_la_reclamation_active_le_comptoir_dans_la_meme_transaction(application, messages):
    from python.engine import claim_patient

    comptoirs, patients, _ = _jeu_de_donnees()
    assert comptoirs[0].is_active is False

    assert claim_patient(patients[0].id, comptoirs[0].id) is True
    assert db.session.get(Counter, comptoirs[0].id).is_active is True


def test_appel_cible_refuse_un_patient_deja_pris(application, messages):
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=2)

    ok, _, statut = calling_service.call_specific(comptoirs[0].id, patients[0].id)
    assert (ok, statut) == (True, 200)

    ok2, charge, statut2 = calling_service.call_specific(comptoirs[1].id, patients[0].id)
    assert ok2 is False
    assert statut2 == 423
    assert charge["error"] == "already_called"


def test_appel_cible_patient_inexistant(application, messages):
    from services import calling_service

    comptoirs, _, _ = _jeu_de_donnees(nb_patients=0)
    ok, charge, statut = calling_service.call_specific(comptoirs[0].id, 99999)
    assert (ok, statut) == (False, 404)
    assert charge["error"] == "not_found"


# --- 2. announce_call : le bloc jadis recopié trois fois ---------------------

def test_announce_call_emet_les_messages_attendus(application, messages):
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees()
    patients[0].counter = comptoirs[0]      # etat posé par la réclamation atomique
    db.session.commit()
    calling_service.announce_call(comptoirs[0].id, patients[0])

    flux = [(s, e) for s, e, _ in messages]
    assert ("update_patient", "update") in flux, "la file des clients web n'est pas rafraichie"
    assert ("update_screen", "add_calling") in flux, "l'annonce d'ecran manque"

    ecran = next(d for s, e, d in messages if e == "add_calling")
    assert ecran["id"] == patients[0].id
    assert ecran["counter_id"] == comptoirs[0].id
    # le gabarit ANNOUNCE_CALL_TEXT a bien ete interprete
    assert str(patients[0].call_number) in ecran["text"]


def test_announce_call_active_le_comptoir(application, messages):
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees()
    patients[0].counter = comptoirs[0]
    db.session.commit()
    calling_service.announce_call(comptoirs[0].id, patients[0])
    assert db.session.get(Counter, comptoirs[0].id).is_active is True


# --- 3. Appel automatique ---------------------------------------------------

def test_auto_calling_ne_plante_pas_sans_patient(application, messages):
    """Régression : l'ancienne version faisait `patient.id` sur la chaine d'erreur.

    ``call_next`` renvoie ``(False, "no_patient")`` quand la file est vide ; le
    code d'origine ne testait pas ce drapeau et levait AttributeError — ce qui
    faisait echouer l'inscription d'un patient.
    """
    from services import calling_service

    _jeu_de_donnees(nb_patients=0, nb_comptoirs=1, auto=True)

    ok, raison = calling_service.run_auto_calling()   # ne doit pas lever
    assert ok is False
    assert raison == "no_counter"


def test_auto_calling_appelle_un_patient(application, messages):
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=1, auto=True)

    ok, patient = calling_service.run_auto_calling()
    assert ok is True
    assert patient.id == patients[0].id
    assert db.session.get(Patient, patients[0].id).status == "calling"
    assert ("app_counter", "update_auto_calling") in [(s, e) for s, e, _ in messages]


def test_auto_calling_ignore_les_comptoirs_sans_personnel(application, messages):
    from services import calling_service

    comptoirs, _, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=1, auto=True,
                                      avec_staff=False)
    assert calling_service.counters_en_appel_automatique() == []


def test_auto_calling_lit_la_base_et_non_une_liste_en_memoire(application, messages):
    """La source de verite est Counter.auto_calling, pas app.config."""
    from services import calling_service

    comptoirs, _, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=1, auto=False)
    assert calling_service.counters_en_appel_automatique() == []

    # bascule en base uniquement : aucune liste a synchroniser
    comptoirs[0].auto_calling = True
    db.session.commit()
    assert [c.id for c in calling_service.counters_en_appel_automatique()] == [comptoirs[0].id]

    assert "AUTO_CALLING" not in application.config, (
        "la liste dupliquee app.config['AUTO_CALLING'] ne doit plus exister"
    )


def test_ordre_des_comptoirs_respecte_counter_order(application, messages):
    from services import calling_service

    comptoirs, _, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=3, auto=True)
    # ordre de tri volontairement inverse de l'ordre d'insertion
    comptoirs[0].sort_order, comptoirs[2].sort_order = 3, 1
    db.session.commit()

    application.config["COUNTER_ORDER"] = "order"
    ordonnes = calling_service.counters_en_appel_automatique()
    assert [c.sort_order for c in ordonnes] == sorted(c.sort_order for c in ordonnes)
    assert ordonnes[0].id == comptoirs[2].id


# --- 4. Enchaînements de haut niveau ----------------------------------------

def test_validate_and_call_next_cloture_puis_appelle(application, messages):
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=2, nb_comptoirs=1)
    comptoir = comptoirs[0]

    ok, premier = calling_service.call_next_for_counter(comptoir.id)
    assert ok is True

    ok2, suivant = calling_service.validate_and_call_next(comptoir.id)
    assert ok2 is True
    assert suivant.id != premier.id
    assert db.session.get(Patient, premier.id).status == "done"
    assert db.session.get(Patient, premier.id).timestamp_end is not None


def test_validate_and_call_next_desactive_le_comptoir_si_file_vide(application, messages):
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=1)
    comptoir = comptoirs[0]

    calling_service.call_next_for_counter(comptoir.id)
    ok, raison = calling_service.validate_and_call_next(comptoir.id)

    assert ok is False
    assert db.session.get(Counter, comptoir.id).is_active is False


def test_validate_current_ne_touche_pas_les_patients_deja_termines(application, messages):
    """Regression de perfomance/statistiques : ne pas reecrire les 'done'."""
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=2, nb_comptoirs=1)
    comptoir = comptoirs[0]

    calling_service.call_next_for_counter(comptoir.id)
    calling_service.validate_current(comptoir.id)
    premier = db.session.get(Patient, patients[0].id)
    horodatage = premier.timestamp_end

    calling_service.call_next_for_counter(comptoir.id)
    touches = calling_service.validate_current(comptoir.id)

    assert premier.id not in [p.id for p in touches]
    assert db.session.get(Patient, patients[0].id).timestamp_end == horodatage


def test_pause_sort_le_comptoir_de_lappel_automatique(application, messages):
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=1, auto=True)
    comptoir = comptoirs[0]

    calling_service.call_next_for_counter(comptoir.id)

    resultat = calling_service.pause(comptoir.id, patients[0].id)

    assert resultat == {"id": None, "counter_id": comptoir.id}
    rafraichi = db.session.get(Counter, comptoir.id)
    assert rafraichi.is_active is False
    assert rafraichi.auto_calling is False, (
        "rester en appel automatique en pause ferait rappeler un patient aussitot"
    )
    assert db.session.get(Patient, patients[0].id).status == "done"
