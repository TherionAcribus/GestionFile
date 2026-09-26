"""P1-6 — clôtures et issues métier (routes ``/api/counter/*``).

Avant ce correctif, ``handle_patient_from_app`` :

- marquait ``done`` **sans** ``timestamp_end`` (durées absentes des stats) ;
- effaçait ``counter`` (les « servis » perdaient leur comptoir dans les
  statistiques du jour) ;
- appliquait toute action sur n'importe quel état, sans idempotence ;
- remettait en file ou transférait sans consigner l'étape close (historique
  de redirection perdu).

Verrouillé ici, avec les **vrais** modèles sur SQLite en mémoire et le vrai
JWT applicatif (``generate_app_token``) :

1. ``validate`` : ``done`` + ``timestamp_end`` + comptoir conservé, rejeu
   sans réécriture ;
2. ``delete`` : ``cancelled`` (issue distincte de ``done``), comptoir
   détaché, étape consignée, parcours borne libéré ;
3. ``standing`` : étape ``requeued``/``transferred`` consignée avant la
   réécriture de l'activité/du comptoir ;
4. patient terminal : toute action est un no-op idempotent ;
5. ``call_next`` : le balayage des ``calling`` pose ``timestamp_end``.
"""

import os
from datetime import datetime, timedelta

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, Counter, Language, Patient, PatientStep, Pharmacist


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="secret-test-closure",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
        ALGO_IS_ACTIVATED=False,
        ALGO_OVERTAKEN_LIMIT=10,
        PHARMACY_NAME="Pharmacie de test",
        ANNOUNCE_CALL_TEXT="Patient {N} au comptoir {C}",
    )
    db.init_app(app)
    from routes.counter import counter_bp
    app.register_blueprint(counter_bp)
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(application):
    return application.test_client()


@pytest.fixture
def token(application):
    from auth_utils import generate_app_token
    with application.app_context():
        return generate_app_token()


@pytest.fixture(autouse=True)
def _silence_temps_reel(monkeypatch):
    """Neutralise la diffusion temps réel : pas de Socket.IO dans ces tests."""
    import communication
    import python.engine as engine
    import routes.counter as counter_module

    noop = lambda *a, **k: None  # noqa: E731
    monkeypatch.setattr(communication, "communikation", noop, raising=False)
    monkeypatch.setattr(counter_module, "communikation", noop, raising=False)
    monkeypatch.setattr(counter_module, "send_app_notification", noop)
    monkeypatch.setattr(engine, "communikation", noop, raising=False)
    monkeypatch.setattr(engine, "notify_patient_phone", noop)
    monkeypatch.setattr(engine, "trigger_async_audio_calling", noop)


def _base(application):
    """Langue + activité + comptoir, renvoie (activity_id, counter_id)."""
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O")
        comptoir = Counter(name="Comptoir 1", sort_order=1)
        db.session.add_all([langue, activite, comptoir])
        db.session.commit()
        return activite.id, comptoir.id


def _patient(application, status="ongoing", counter_id=None,
             activity_id=None, journey_id=None):
    with application.app_context():
        langue = Language.query.first()
        activite = Activity.query.first()
        patient = Patient(
            call_number="A1", status=status,
            activity_id=activity_id or activite.id,
            counter_id=counter_id, language_id=langue.id,
            journey_id=journey_id,
            timestamp=datetime.now() - timedelta(minutes=15),
            timestamp_counter=(datetime.now() - timedelta(minutes=2)
                               if counter_id else None),
        )
        db.session.add(patient)
        db.session.commit()
        return patient.id


def _relu(application, patient_id):
    with application.app_context():
        return db.session.get(Patient, patient_id)


def _etapes(application, patient_id):
    with application.app_context():
        return (PatientStep.query.filter_by(patient_id=patient_id)
                .order_by(PatientStep.id).all())


# --- validate ----------------------------------------------------------------

def test_validate_pose_timestamp_end_et_garde_le_comptoir(client, application, token):
    """Régression : la validation App passait en 'done' sans timestamp_end
    (hors stats de durée) et perdait le comptoir (hors stats par comptoir)."""
    _, comptoir_id = _base(application)
    pid = _patient(application, status="ongoing", counter_id=comptoir_id)

    reponse = client.post(f"/api/counter/validate_patient/{pid}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    patient = _relu(application, pid)
    assert patient.status == "done"
    assert patient.timestamp_end is not None
    assert patient.counter_id == comptoir_id


def test_validate_rejeu_ne_reecrit_pas_timestamp_end(client, application, token):
    _, comptoir_id = _base(application)
    pid = _patient(application, status="ongoing", counter_id=comptoir_id)
    client.post(f"/api/counter/validate_patient/{pid}",
                headers={"X-App-Token": token})
    premiere_fin = _relu(application, pid).timestamp_end

    import time
    time.sleep(0.01)
    client.post(f"/api/counter/validate_patient/{pid}",
                headers={"X-App-Token": token})

    assert _relu(application, pid).timestamp_end == premiere_fin


def test_validate_sur_deja_fait_est_un_noop(client, application, token):
    """Patient déjà 'done' : re-soumission = succès idempotent, rien n'est
    réécrit (ni le statut, ni la fin, ni le comptoir)."""
    _, comptoir_id = _base(application)
    pid = _patient(application, status="done", counter_id=comptoir_id)

    reponse = client.post(f"/api/counter/validate_patient/{pid}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    assert _relu(application, pid).status == "done"
    assert _etapes(application, pid) == []


# --- delete (retrait par le personnel) ----------------------------------------

def test_delete_marque_annule_et_consigne_etape(client, application, token):
    """« Supprimer » n'est pas « servi » : issue 'cancelled', comptoir
    détaché, et l'étape au comptoir est consignée (elle disparaissait)."""
    activite_id, comptoir_id = _base(application)
    pid = _patient(application, status="ongoing", counter_id=comptoir_id,
                   activity_id=activite_id)

    reponse = client.post(f"/api/counter/delete_patient/{pid}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    patient = _relu(application, pid)
    assert patient.status == "cancelled"
    assert patient.timestamp_end is not None
    assert patient.counter_id is None

    etapes = _etapes(application, pid)
    assert len(etapes) == 1
    assert etapes[0].outcome == "cancelled"
    assert etapes[0].counter_id == comptoir_id
    assert etapes[0].activity_id == activite_id


def test_delete_patient_inconnu_404(client, token):
    reponse = client.post("/api/counter/delete_patient/99999",
                          headers={"X-App-Token": token})
    assert reponse.status_code == 404


def test_delete_pending_libere_le_parcours(client, application, token):
    """Inscription encore 'pending' (ticket jamais imprimé) retirée par le
    personnel : 'cancelled' et journey_id libéré pour un nouveau scan."""
    _base(application)
    pid = _patient(application, status="pending", journey_id="j-annule")

    client.post(f"/api/counter/delete_patient/{pid}",
                headers={"X-App-Token": token})

    patient = _relu(application, pid)
    assert patient.status == "cancelled"
    assert patient.journey_id is None


def test_delete_sur_annule_est_un_noop(client, application, token):
    """Rejeu d'un retrait : la première issue fait foi, pas de seconde étape."""
    _, comptoir_id = _base(application)
    pid = _patient(application, status="ongoing", counter_id=comptoir_id)
    client.post(f"/api/counter/delete_patient/{pid}",
                headers={"X-App-Token": token})

    reponse = client.post(f"/api/counter/delete_patient/{pid}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    assert len(_etapes(application, pid)) == 1


def test_delete_sur_deja_servi_ne_degrade_pas_l_issue(client, application, token):
    """Un patient 'done' retiré par erreur après coup : l'issue 'servi'
    enregistrée n'est pas réécrite en 'cancelled'."""
    _, comptoir_id = _base(application)
    pid = _patient(application, status="done", counter_id=comptoir_id)

    reponse = client.post(f"/api/counter/delete_patient/{pid}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    patient = _relu(application, pid)
    assert patient.status == "done"
    assert _etapes(application, pid) == []


# --- standing (renvoi en file / transfert) -------------------------------------

def test_remise_en_file_conserve_l_etape(client, application, token):
    """Renvoi en file : le passage au comptoir est consigné ('requeued')
    avant que le rattachement ne soit effacé."""
    activite_id, comptoir_id = _base(application)
    pid = _patient(application, status="ongoing", counter_id=comptoir_id,
                   activity_id=activite_id)

    reponse = client.post(f"/api/counter/put_standing_list/{pid}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    patient = _relu(application, pid)
    assert patient.status == "standing"
    assert patient.counter_id is None
    assert patient.timestamp_end is None  # le parcours continue

    etapes = _etapes(application, pid)
    assert len(etapes) == 1
    assert etapes[0].outcome == "requeued"
    assert etapes[0].counter_id == comptoir_id
    assert etapes[0].activity_id == activite_id
    assert etapes[0].timestamp_counter is not None


def test_transfert_vers_autre_activite_conserve_les_deux_bouts(
        client, application, token):
    """Redirection : l'activité d'origine et la cible sont consignées —
    plus de perte d'historique au changement d'activité."""
    activite_id, comptoir_id = _base(application)
    with application.app_context():
        autre = Activity(name="Conseil", letter="C")
        db.session.add(autre)
        db.session.commit()
        cible_id = autre.id

    pid = _patient(application, status="calling", counter_id=comptoir_id,
                   activity_id=activite_id)
    reponse = client.post(f"/api/counter/put_standing_list/{pid}/{cible_id}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    patient = _relu(application, pid)
    assert patient.status == "standing"
    assert patient.activity_id == cible_id

    etapes = _etapes(application, pid)
    assert len(etapes) == 1
    assert etapes[0].outcome == "transferred"
    assert etapes[0].activity_id == activite_id
    assert etapes[0].new_activity_id == cible_id
    assert etapes[0].counter_id == comptoir_id


def test_remise_en_file_rejeu_ne_duplique_pas_l_etape(client, application, token):
    """Patient déjà 'standing' à la même activité : rejeu sans nouvelle
    étape (l'attente initiale n'était pas une étape à clôturer)."""
    _base(application)
    pid = _patient(application, status="standing")

    reponse = client.post(f"/api/counter/put_standing_list/{pid}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    assert _etapes(application, pid) == []


def test_transfert_vers_activite_inconnue_404(client, application, token):
    _base(application)
    pid = _patient(application, status="standing")

    reponse = client.post(f"/api/counter/put_standing_list/{pid}/99999",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 404
    assert _relu(application, pid).status == "standing"
    assert _etapes(application, pid) == []


def test_remise_en_file_sur_terminal_est_un_noop(client, application, token):
    """Un patient 'done' ne peut pas revenir en file via l'App : la
    résurrection silencieuse perdait l'issue enregistrée."""
    _, comptoir_id = _base(application)
    pid = _patient(application, status="done", counter_id=comptoir_id)

    reponse = client.post(f"/api/counter/put_standing_list/{pid}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    assert _relu(application, pid).status == "done"


# --- call_next : balayage des 'calling' -----------------------------------------

def test_call_next_balayage_pose_timestamp_end(application):
    """Le patient appelé puis dépassé (le comptoir appelle le suivant)
    recevait 'done' sans fin horodatée : hors de toute statistique de durée."""
    from python.engine import call_next

    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O")
        membre = Pharmacist(name="Membre", initials="M1")
        membre.activities.append(activite)
        comptoir = Counter(name="Comptoir 1", sort_order=1, staff=membre)
        db.session.add_all([langue, activite, membre, comptoir])
        db.session.flush()
        depasse = Patient(call_number="A1", status="calling",
                          activity_id=activite.id, language_id=langue.id,
                          counter_id=comptoir.id)
        suivant = Patient(call_number="A2", status="standing",
                          activity_id=activite.id, language_id=langue.id)
        db.session.add_all([depasse, suivant])
        db.session.commit()
        depasse_id, comptoir_id = depasse.id, comptoir.id

        ok, appele = call_next(comptoir_id)

        assert ok is True
        assert appele.id == suivant.id
        ferme = db.session.get(Patient, depasse_id)
        assert ferme.status == "done"
        assert ferme.timestamp_end is not None
        assert ferme.counter_id == comptoir_id


def test_sans_token_les_routes_app_refusent(client, application):
    """Garde-fou : sans jeton applicatif ni session, rien ne mute."""
    _base(application)
    pid = _patient(application, status="ongoing")

    reponse = client.post(f"/api/counter/validate_patient/{pid}")

    assert reponse.status_code == 401
    assert _relu(application, pid).status == "ongoing"
