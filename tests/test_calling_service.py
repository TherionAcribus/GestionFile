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
    monkeypatch.setattr(service, "notify_patient_phone_closed", lambda *a, **k: None)
    monkeypatch.setattr(service, "send_app_notification", lambda *a, **k: None)
    monkeypatch.setattr(engine, "notify_patient_phone", lambda *a, **k: None)
    monkeypatch.setattr(engine, "notify_patient_phone_closed", lambda *a, **k: None)
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


def test_reclamation_arme_validation_automatique(application, messages):
    from python.engine import claim_patient

    application.config.update(
        CALL_AUTO_VALIDATION_ENABLED=True,
        CALL_AUTO_VALIDATION_WARNING_SECONDS=60,
        CALL_AUTO_VALIDATION_GRACE_SECONDS=30,
    )
    comptoirs, patients, _ = _jeu_de_donnees()

    assert claim_patient(patients[0].id, comptoirs[0].id) is True
    patient = db.session.get(Patient, patients[0].id)
    assert patient.call_auto_validate_at is not None
    assert patient.call_extension_count == 0


def test_prolongation_repousse_echeance_sans_relancer_annonce(application, messages):
    from datetime import datetime, timedelta
    from config import time_tz
    from services import call_auto_validation

    application.config.update(
        CALL_AUTO_VALIDATION_ENABLED=True,
        CALL_AUTO_VALIDATION_WARNING_SECONDS=60,
        CALL_AUTO_VALIDATION_GRACE_SECONDS=30,
        CALL_AUTO_VALIDATION_EXTENSION_SECONDS=120,
    )
    comptoirs, patients, _ = _jeu_de_donnees()
    now = datetime.now(time_tz).replace(tzinfo=None)
    patient = patients[0]
    patient.status = "calling"
    patient.counter_id = comptoirs[0].id
    patient.call_auto_validate_at = now + timedelta(seconds=10)
    db.session.commit()

    ok, payload, status = call_auto_validation.extend_call(
        comptoirs[0].id, patient.id, now=now)

    assert (ok, status) == (True, 200)
    assert payload["call_extension_count"] == 1
    assert db.session.get(Patient, patient.id).call_auto_validate_at == (
        now + timedelta(seconds=120))
    assert not any(event == "audio" for _stream, event, _data in messages)


def test_seule_echeance_expiree_est_validee_automatiquement(application, messages):
    from datetime import datetime, timedelta
    from config import time_tz
    from services import call_auto_validation

    application.config.update(CALL_AUTO_VALIDATION_ENABLED=True)
    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=2)
    now = datetime.now(time_tz).replace(tzinfo=None)
    expire, futur = patients
    for patient in patients:
        patient.status = "calling"
        patient.counter_id = comptoirs[0].id
    expire.call_auto_validate_at = now - timedelta(seconds=1)
    futur.call_auto_validate_at = now + timedelta(seconds=60)
    db.session.commit()

    validated = call_auto_validation.auto_validate_expired(now=now)

    assert validated == [expire.id]
    assert db.session.get(Patient, expire.id).status == "ongoing"
    assert db.session.get(Patient, expire.id).call_auto_validate_at is None
    assert db.session.get(Patient, futur.id).status == "calling"


def test_tache_planifiee_valide_et_notifie_les_echeances(application, messages, monkeypatch):
    """``call_auto_validation_job`` — le maillon réellement exécuté par
    APScheduler toutes les 5 s (AppHolder, rechargement de config) — doit
    valider le patient expiré ET émettre ``counter_state_changed`` : sans cet
    évènement, l'App Comptoir garde l'échéance périmée et clignote sans fin."""
    from datetime import datetime, timedelta
    from app_holder import AppHolder
    from config import time_tz
    from scheduler_functions import call_auto_validation_job
    import services.call_auto_validation as service_echeances

    def faux_communikation(stream, data=None, flag=None, event="update", client_id=None):
        messages.append((stream, event, data))

    monkeypatch.setattr(service_echeances, "communikation", faux_communikation)

    application.config.update(CALL_AUTO_VALIDATION_ENABLED=True)
    comptoirs, patients, _ = _jeu_de_donnees()
    patient = patients[0]
    patient.status = "calling"
    patient.counter_id = comptoirs[0].id
    patient.call_auto_validate_at = (
        datetime.now(time_tz).replace(tzinfo=None) - timedelta(seconds=1))
    db.session.commit()

    AppHolder.set_app(application)
    try:
        call_auto_validation_job()
    finally:
        AppHolder.set_app(None)

    rafraichi = db.session.get(Patient, patient.id)
    assert rafraichi.status == "ongoing"
    assert rafraichi.call_auto_validate_at is None
    assert ("update_patient", "update") in [(s, e) for s, e, _ in messages]
    assert ("app_counter", "counter_state_changed") in [
        (s, e) for s, e, _ in messages]


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


# --- 1b. « Terminer puis appeler » atomique ---------------------------------

def test_appel_cible_inexistant_ne_cloture_pas_le_courant(application, messages):
    """Régression : l'appel ciblé validait d'abord le patient en cours, PUIS
    vérifiait la cible — un 404 clôturait quand même la prise en charge."""
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=1)
    comptoir = comptoirs[0]

    calling_service.call_next_for_counter(comptoir.id)
    courant = patients[0]
    courant.status = "ongoing"
    db.session.commit()

    ok, _, statut = calling_service.call_specific(comptoir.id, 99999)

    assert (ok, statut) == (False, 404)
    rafraichi = db.session.get(Patient, courant.id)
    assert rafraichi.status == "ongoing", "le patient courant a été clôturé par un appel qui a échoué"
    assert rafraichi.timestamp_end is None


def test_appel_cible_deja_pris_ne_cloture_pas_le_courant(application, messages):
    """Cible déjà réclamée ailleurs -> 423, et le patient du comptoir reste
    en cours (l'échec ne doit clôturer aucune donnée)."""
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=2, nb_comptoirs=2)
    c1, c2 = comptoirs
    courant, cible = patients

    # c1 a un patient en cours ; c2 a déjà réclamé la cible.
    courant.status, courant.counter_id = "ongoing", c1.id
    cible.status, cible.counter_id = "calling", c2.id
    db.session.commit()

    ok, charge, statut = calling_service.call_specific(c1.id, cible.id)

    assert (ok, statut) == (False, 423)
    assert charge["error"] == "already_called"
    rafraichi = db.session.get(Patient, courant.id)
    assert rafraichi.status == "ongoing"
    assert rafraichi.timestamp_end is None


def test_appel_cible_cloture_et_reclame_en_une_transition(application, messages):
    """Cas nominal : le patient en cours est clôturé et la cible appelée —
    dans le même commit."""
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=2, nb_comptoirs=1)
    comptoir = comptoirs[0]
    courant, cible = patients
    courant.status, courant.counter_id = "ongoing", comptoir.id
    db.session.commit()

    ok, charge, statut = calling_service.call_specific(comptoir.id, cible.id)

    assert (ok, statut) == (True, 200)
    assert db.session.get(Patient, courant.id).status == "done"
    assert db.session.get(Patient, courant.id).timestamp_end is not None
    rafraichi = db.session.get(Patient, cible.id)
    assert rafraichi.status == "calling"
    assert rafraichi.counter_id == comptoir.id


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


# --- 5. Arrivée au comptoir (calling -> ongoing) -----------------------------

def test_arrivee_valide_le_patient_appele(application, messages):
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=1)
    calling_service.call_next_for_counter(comptoirs[0].id)

    ok, charge, statut = calling_service.arrive_at_counter(comptoirs[0].id, patients[0].id)

    assert (ok, statut) == (True, 200)
    rafraichi = db.session.get(Patient, patients[0].id)
    assert rafraichi.status == "ongoing"
    assert rafraichi.timestamp_counter is not None


def test_arrivee_refuse_un_patient_termine(application, messages):
    """Un patient 'done' ne doit pas revenir en 'ongoing' — l'ancienne route
    réécrivait status et timestamp_counter sans aucun contrôle."""
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=1)
    patients[0].status = "done"
    db.session.commit()

    ok, _, statut = calling_service.arrive_at_counter(comptoirs[0].id, patients[0].id)

    assert (ok, statut) == (False, 409)
    rafraichi = db.session.get(Patient, patients[0].id)
    assert rafraichi.status == "done"
    assert rafraichi.timestamp_counter is None


# --- 6. Robustesse du chemin d'inscription borne -----------------------------
#
# Le flux borne commit le patient 'standing' PUIS fait run_auto_calling() et
# communikation("update_patient"). Une exception dans l'appel automatique ne
# doit jamais empêcher la diffusion — sinon le patient est en base, visible en
# Admin, mais jamais poussé aux App Comptoir (incident « borne → App »).

def test_run_auto_calling_ne_propage_pas_une_erreur_de_comptoir(application, messages, monkeypatch):
    """Le service s'exécute avant la diffusion de la file : même si la
    réclamation lève (comptoir défectueux), l'exception ne doit pas sortir —
    sinon communikation("update_patient") n'est jamais atteint."""
    from services import calling_service

    _jeu_de_donnees(nb_patients=1, nb_comptoirs=1, auto=True)

    def panne(*args, **kwargs):
        raise RuntimeError("panne interne")

    monkeypatch.setattr(calling_service, "call_next_for_counter", panne)

    ok, raison = calling_service.run_auto_calling()   # ne doit pas lever
    assert (ok, raison) == (False, "no_counter")


def test_auto_calling_survit_a_un_staff_supprime(application, messages):
    """Counter.staff_id peut pointer vers un pharmacien supprimé : la relation
    vaut None et l'ancien algo plantait sur ``counter.staff.activities``."""
    from services import calling_service

    comptoirs, _, _ = _jeu_de_donnees(
        nb_patients=1, nb_comptoirs=1, auto=True, avec_staff=False)
    comptoirs[0].staff_id = 99999   # FK pendante (SQLite n'impose pas la FK)
    db.session.commit()

    ok, raison = calling_service.run_auto_calling()   # ne doit pas lever
    assert (ok, raison) == (False, "no_counter")


def test_register_patient_diffuse_meme_si_appel_auto_plante(application, messages, monkeypatch):
    """Régression « borne → App Comptoir » : le patient est commité 'standing'
    AVANT l'appel automatique ; une panne de celui-ci ne doit pas empêcher
    communikation("update_patient") — sinon le patient reste invisible de
    l'App Comptoir jusqu'à la prochaine diffusion quelconque (ajout admin,
    appel du suivant…)."""
    import services.calling_service as service
    from python.engine import register_patient

    _jeu_de_donnees(nb_patients=0, nb_comptoirs=0)
    activite = Activity.query.first()

    def panne():
        raise RuntimeError("panne interne")

    monkeypatch.setattr(service, "run_auto_calling", panne)

    # add_patient lit la session Flask (langue choisie sur la borne).
    with application.test_request_context():
        patient = register_patient(activite)

    assert patient.status == "standing"
    assert ("update_patient", "update") in [(s, e) for s, e, _ in messages], (
        "la diffusion de la file doit partir malgré la panne de "
        "l'appel automatique"
    )


def test_arrivee_refuse_le_mauvais_comptoir(application, messages):
    """Le patient appelé par le comptoir 2 ne peut pas être validé depuis le
    comptoir 1."""
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=2)
    patients[0].status, patients[0].counter_id = "calling", comptoirs[1].id
    db.session.commit()

    ok, _, statut = calling_service.arrive_at_counter(comptoirs[0].id, patients[0].id)

    assert (ok, statut) == (False, 409)
    rafraichi = db.session.get(Patient, patients[0].id)
    assert rafraichi.status == "calling"
    assert rafraichi.counter_id == comptoirs[1].id


def test_arrivee_rejouee_ne_reecrit_pas_lhorodatage(application, messages):
    """Double validation (rejeu réseau, double-clic) : succès sans écraser
    timestamp_counter — sinon la durée d'attente réelle était faussée."""
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=1)
    calling_service.call_next_for_counter(comptoirs[0].id)

    ok1, _, _ = calling_service.arrive_at_counter(comptoirs[0].id, patients[0].id)
    horodatage = db.session.get(Patient, patients[0].id).timestamp_counter
    ok2, _, statut2 = calling_service.arrive_at_counter(comptoirs[0].id, patients[0].id)

    assert (ok1, ok2, statut2) == (True, True, 200)
    assert db.session.get(Patient, patients[0].id).timestamp_counter == horodatage


def test_pause_ne_cloture_pas_le_patient_dun_autre_comptoir(application, messages):
    """pause(comptoir, patient_id) clôturait n'importe quel patient —
    y compris celui d'un autre comptoir ou déjà terminé."""
    from services import calling_service

    comptoirs, patients, _ = _jeu_de_donnees(nb_patients=1, nb_comptoirs=2)
    patients[0].status, patients[0].counter_id = "ongoing", comptoirs[1].id
    db.session.commit()

    calling_service.pause(comptoirs[0].id, patients[0].id)

    rafraichi = db.session.get(Patient, patients[0].id)
    assert rafraichi.status == "ongoing"
    assert rafraichi.timestamp_end is None


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
