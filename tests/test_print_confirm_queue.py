"""File locale durable des acquittements d'impression (borne patient).

Avant : après une impression physique réussie, ``/patient/confirm_print``
n'était tenté qu'UNE fois. Si le réseau tombait entre l'impression et
l'acquittement, le patient repartait avec un ticket absent de la file.

Désormais l'acquittement est persisté en ``localStorage`` AVANT le premier
POST, puis retenté jusqu'à réponse définitive — ``confirm_print`` est
idempotent côté serveur. La vidange reprend au retour du réseau (connect
Socket.IO), périodiquement, et après un rechargement/redémarrage de la borne.

Verrouillé ici :

1. ``patients.js`` : l'acquittement est enfilé AVANT le premier POST, la file
   est durable (localStorage + repli mémoire), les 5xx restent en file, et la
   vidange est déclenchée sur connect Socket.IO + chargement + minuterie ;
2. ``handlePrintConfirmation`` traite ``standing`` comme un succès (réponse
   perdue en vol puis retentée : le patient est déjà en file) ;
3. ``/patient/confirm_print`` renvoie un état MÉTIER idempotent
   ('activated'/'cancelled'), pas le statut interne brut.
"""

import os
import re

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, Language, Patient

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _lire(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. patients.js — file durable et réessais
# ---------------------------------------------------------------------------

def _corps_fonction(source, nom):
    """Corps d'une fonction : de sa déclaration à la suivante de même niveau."""
    debut = source.index(f"function {nom}(")
    m = re.search(r"\nfunction \w+\(", source[debut:])
    return source[debut:debut + m.start()] if m else source[debut:]


def test_acquittement_enfile_avant_le_premier_post():
    """Dans runPrintFlow, l'acquittement doit être persisté AVANT le premier
    POST : une coupure entre impression et POST ne perd plus l'inscription."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "runPrintFlow")
    assert "enqueuePrintConfirmation" in corps
    assert corps.index("enqueuePrintConfirmation") < corps.index(
        "postPrintConfirmation"), (
        "enqueuePrintConfirmation doit précéder postPrintConfirmation")


def test_file_persistee_en_localstorage_avec_repli():
    """La file survit au rechargement/redémarrage (localStorage), avec un
    repli mémoire si le stockage est indisponible."""
    source = _lire("static/js/patients.js")
    assert "localStorage" in source
    assert "PRINT_QUEUE_KEY" in source
    assert "_memPrintQueue" in source


def test_5xx_reste_en_file():
    """Un 5xx laisse l'état serveur inconnu : le job doit être retenté."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "postPrintConfirmation")
    assert "r.status >= 500" in corps


def test_vidange_declenchee_sur_connect_chargement_et_minuterie():
    source = _lire("static/js/patients.js")
    # connect Socket.IO (réseau de retour)
    assert re.search(r"on\('connect'[^}]*drainPrintConfirmations", source, re.S)
    # chargement de page + minuterie périodique
    assert re.search(r"DOMContentLoaded[^}]*drainPrintConfirmations", source, re.S)
    assert "setInterval(drainPrintConfirmations" in source


def test_vidange_ne_met_a_jour_que_le_job_affiche():
    """Un job ancien est acquitté silencieusement : seul le job affiché met à
    jour l'écran (via displayedPrintJobId)."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "drainPrintConfirmations")
    assert "displayedPrintJobId" in corps
    assert "dequeuePrintConfirmation" in corps


def test_standing_interprete_comme_succes():
    """'standing' = patient déjà en file (réponse perdue en vol) : écran de
    confirmation normal, pas d'échec."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "handlePrintConfirmation")
    assert re.search(r"case 'activated':\s*\n\s*case 'standing':", corps)


# ---------------------------------------------------------------------------
# 2. /patient/confirm_print — idempotence métier
# ---------------------------------------------------------------------------

@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY="secret-test-print-queue",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # db.metadatas est partagé entre fichiers de test : le bind 'users'
        # peut déjà y être déclaré (cf. test_phone_patient_token).
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
    )
    db.init_app(app)
    from routes.patient import patient_bp
    app.register_blueprint(patient_bp)
    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(application):
    return application.test_client()


def _pending_patient(application, print_job_id="job-1"):
    """Crée une inscription EN ATTENTE d'acquittement d'impression."""
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O",
                            notification=False, specific_message="")
        patient = Patient(call_number="A1", status="pending",
                          activity=activite, language=langue,
                          print_job_id=print_job_id)
        db.session.add_all([langue, activite, patient])
        db.session.commit()
        return patient.id


def test_confirm_print_idempotent_apres_activation(client, application):
    """Ticket imprimé + réseau revenu : le réessai obtient 'activated' — état
    métier, pas le statut interne 'standing' qui serait lu comme un échec."""
    _pending_patient(application)

    r1 = client.post("/patient/confirm_print",
                     json={"print_job_id": "job-1", "success": True})
    assert r1.get_json()["status"] == "activated"

    r2 = client.post("/patient/confirm_print",
                     json={"print_job_id": "job-1", "success": True})
    assert r2.status_code == 200
    assert r2.get_json()["status"] == "activated"

    # Et le patient n'est activé qu'une fois.
    with application.app_context():
        patient = Patient.query.filter_by(print_job_id="job-1").one()
        assert patient.status == "standing"
        assert Patient.query.filter_by(status="standing").count() == 1


def test_confirm_print_idempotent_apres_annulation(client, application):
    """Échec + comportement 'cancel' : le réessai obtient 'cancelled' (le
    statut interne 'print_failed' n'est pas exposé)."""
    application.config["PAGE_PATIENT_PRINT_FAIL_BEHAVIOR"] = "cancel"
    _pending_patient(application)

    r1 = client.post("/patient/confirm_print",
                     json={"print_job_id": "job-1", "success": False})
    assert r1.get_json()["status"] == "cancelled"

    r2 = client.post("/patient/confirm_print",
                     json={"print_job_id": "job-1", "success": False})
    assert r2.get_json()["status"] == "cancelled"


def test_confirm_print_ask_reste_idempotent(client, application):
    """Échec + comportement 'ask' (défaut) : le patient reste pending et le
    réessai renvoie les mêmes options de décision."""
    _pending_patient(application)

    for _ in range(2):
        r = client.post("/patient/confirm_print",
                        json={"print_job_id": "job-1", "success": False})
        assert r.get_json()["status"] == "ask"
    with application.app_context():
        patient = Patient.query.filter_by(print_job_id="job-1").one()
        assert patient.status == "pending"


def test_confirm_print_job_inconnu_ou_purge(client, application):
    """Job inconnu (pending expiré/purgé) : 'expired' — réponse définitive,
    la borne sort le job de la file."""
    r = client.post("/patient/confirm_print",
                    json={"print_job_id": "inconnu", "success": True})
    assert r.status_code == 410
    assert r.get_json()["status"] == "expired"


# ---------------------------------------------------------------------------
# 3. Inscriptions expirées : réconciliation des confirmations tardives
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta


def _patient_expire(application, print_job_id="job-x", jours=0, journey="j-x"):
    """Inscription passée en 'expired' (résultat d'impression inconnu), dont
    l'acquittement arrive tardivement."""
    from config import time_tz
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O",
                            notification=False, specific_message="")
        patient = Patient(
            call_number="A9", status="expired",
            activity=activite, language=langue,
            print_job_id=print_job_id, journey_id=journey,
            timestamp=datetime.now(time_tz) - timedelta(days=jours))
        db.session.add_all([langue, activite, patient])
        db.session.commit()
        return patient.id


def test_expiration_marque_au_lieu_de_supprimer(application):
    """Le TTL ne supprime plus la ligne : 'expired' garde la trace du travail
    d'impression pour la réconciliation, et libère le parcours."""
    from python.engine import expire_stale_pending_patients
    from config import time_tz

    pid = _pending_patient(application)
    with application.app_context():
        patient = Patient.query.get(pid)
        patient.journey_id = "j-ttl"
        patient.timestamp = datetime.now(time_tz) - timedelta(seconds=9999)
        db.session.commit()

        assert expire_stale_pending_patients() == 1
        patient = Patient.query.get(pid)
        assert patient.status == "expired", "l'inscription était supprimée au lieu d'expirer"
        assert patient.journey_id is None


def test_confirmation_tardive_jour_meme_reconcilie(client, application):
    """Ticket imprimé puis coupure > TTL : l'acquittement tardif remet le
    patient en file (même journée) — avant : 410 et patient perdu avec son
    ticket."""
    _patient_expire(application)

    r = client.post("/patient/confirm_print",
                    json={"print_job_id": "job-x", "success": True})

    donnees = r.get_json()
    assert r.status_code == 200
    assert donnees["status"] == "activated"
    assert donnees["reconciled"] is True
    with application.app_context():
        patient = Patient.query.filter_by(print_job_id="job-x").one()
        assert patient.status == "standing"


def test_confirmation_tardive_sur_jour_passe_reste_expiree(client, application):
    """Un acquittement arrivant le lendemain (ou après purge) ne fait pas
    entrer en file une inscription d'hier."""
    _patient_expire(application, jours=1)

    r = client.post("/patient/confirm_print",
                    json={"print_job_id": "job-x", "success": True})

    assert r.status_code == 410
    assert r.get_json()["status"] == "expired"


def test_echec_tardif_sur_expiree_reste_expiree(client, application):
    """Résultat enfin connu = échec : la demande reste expirée, jamais
    entrée en file."""
    _patient_expire(application)

    r = client.post("/patient/confirm_print",
                    json={"print_job_id": "job-x", "success": False})

    assert r.status_code == 410
    with application.app_context():
        assert Patient.query.filter_by(print_job_id="job-x").one().status == "expired"


def test_abandon_puis_succes_tardif_ne_ressuscite_pas(client, application):
    """Abandon tranché d'abord, succès tardif ensuite : 'cancelled', pas
    de résurrection — un seul des deux peut gagner."""
    _pending_patient(application)

    client.post("/patient/print_abandon", json={"print_job_id": "job-1"})
    r = client.post("/patient/confirm_print",
                    json={"print_job_id": "job-1", "success": True})

    assert r.get_json()["status"] == "cancelled"
    with application.app_context():
        assert Patient.query.filter_by(print_job_id="job-1").one().status == "print_failed"


# ---------------------------------------------------------------------------
# 4. JS : pas d'annonce de succès non confirmée
# ---------------------------------------------------------------------------

def test_call_staff_n_annonce_pas_un_succes_non_confirme():
    """En cas d'erreur réseau ou de réponse sans 'staff_called', la borne
    doit proposer Réessayer/Retour — jamais « personnel prévenu »."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "callStaffFlow")

    # Le succès n'est affiché que sur staff_called explicite du serveur.
    assert "staff_called" in corps
    # Les deux voies d'échec passent par callStaffFailed.
    assert corps.count("callStaffFailed") >= 2

    echec = _corps_fonction(_lire("static/js/patients.js"), "callStaffFailed")
    assert "print_failed_staff" in echec
    assert "retry" in echec
    assert "staff_called" not in echec
