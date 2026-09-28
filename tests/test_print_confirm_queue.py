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
    """Corps d'une fonction JS ou Python : de sa déclaration à la suivante."""
    marker = f"function {nom}(" if f"function {nom}(" in source else f"def {nom}("
    debut = source.index(marker)
    m = re.search(r"\n(?:function \w+|def \w+)\(", source[debut:])
    return source[debut:debut + m.start()] if m else source[debut:]


def test_acquittement_enfile_avant_le_premier_post():
    """L'acquittement doit être persisté AVANT le premier POST : une coupure
    entre impression et POST ne perd plus l'inscription."""
    source = _lire("static/js/patients.js")
    flux = _corps_fonction(source, "runPrintFlow")
    assert "_runPrintAttempt" in flux
    tentative = _corps_fonction(source, "_runPrintAttempt")
    assert "confirmPrintResult" in tentative
    corps = _corps_fonction(source, "confirmPrintResult")
    assert "enqueuePrintConfirmation" in corps
    assert corps.index("enqueuePrintConfirmation") < corps.index(
        "postPrintConfirmation"), (
        "enqueuePrintConfirmation doit précéder postPrintConfirmation")


def test_busy_n_est_jamais_confirme_comme_un_echec():
    """'busy' signifie « cet appel n'a pas été envoyé » : le flux retente
    localement de façon bornée au lieu de POSTer un faux échec qui pourrait
    annuler une inscription pendant qu'un autre tirage occupe l'imprimante."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "_runPrintAttempt")
    assert "result.code === 'busy'" in corps
    assert "result.attempted === false" in corps
    assert "PRINT_BUSY_MAX_ATTEMPTS" in corps
    assert "busy_timeout" in corps


def test_impression_automatique_dedupee_par_job():
    """Un re-rendu HTMX du même fragment ne doit pas réimprimer. Seul le
    bouton « Réessayer » (options.manual) peut relancer le job."""
    source = _lire("static/js/patients.js")
    flux = _corps_fonction(source, "runPrintFlow")
    assert "_automaticPrintJobs[printJobId]" in flux
    assert "options.manual" in flux
    # Un rejeu ignoré ne doit pas écraser l'écran de choix par l'overlay busy.
    assert flux.index("_automaticPrintJobs[printJobId]") < flux.index("showPrintBusy")
    reponse = _corps_fonction(source, "handlePrintConfirmation")
    assert "{ manual: true }" in reponse
    assert "{ automatic: true }" in source


def test_rechargement_ne_rejoue_pas_le_tirage_physique():
    """La marque de tentative est posée avant le pont : un nouveau rendu
    automatique rejoue l'acquittement connu — ou un résultat incertain — mais
    ne rappelle jamais sendPrintTicket. Seul le clic manuel peut réimprimer."""
    source = _lire("static/js/patients.js")
    flux = _corps_fonction(source, "runPrintFlow")
    assert "getPrintAttempt(printJobId)" in flux
    assert "markPrintAttempt(printJobId)" in flux
    assert flux.index("markPrintAttempt") < flux.index("_runPrintAttempt")
    assert "print_interrupted" in flux
    assert "confirmPrintResult" in flux
    assert "sendPrintTicket" not in flux
    confirmation = _corps_fonction(source, "confirmPrintResult")
    assert "recordPrintAttemptResult" in confirmation
    assert confirmation.index("recordPrintAttemptResult") < confirmation.index(
        "enqueuePrintConfirmation")
    assert "PRINT_ATTEMPT_KEY" in source
    assert "localStorage" in _corps_fonction(source, "_writePrintAttempts")


def test_aucun_rejeu_sur_erreur_d_arite_du_pont():
    """La compatibilité ancienne borne se fait par capacité déclarée, pas par
    nouvel appel après TypeError : ce dernier peut arriver après impression."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "sendPrintTicket")
    assert "supportsPrintJobId" in corps
    assert "/positional|argument/" not in corps
    assert "printerApi.print_ticket(printData);" in corps


def test_sans_print_job_id_aucune_impression_automatique():
    """Sans clé d'acquittement, imprimer serait impossible à réconcilier et un
    re-rendu pourrait multiplier les tickets : la borne refuse le tirage."""
    source = _lire("static/js/patients.js")
    assert "sendPrintTicket(printData);" not in source
    assert "missing-print-job" in source


def test_pont_d_impression_ne_peut_pas_figer_le_verrou_js():
    """Une promesse pywebview jamais résolue ne doit pas laisser
    ``_printInProgress`` vrai indéfiniment : une borne de temps retourne un
    résultat INCERTAIN, car le ticket peut être sorti malgré le silence."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "sendPrintTicket")
    assert "PRINT_BRIDGE_TIMEOUT_MS" in corps
    assert "Promise.race" in corps
    assert "bridge_timeout" in corps
    assert "maybe_printed: true" in corps
    assert ".finally" in corps


def test_resultat_inconnu_est_propage_et_tranche_par_le_patient():
    """maybe_printed doit traverser la file et produire un écran 'uncertain'
    avec les choix explicites ticket reçu / ticket absent / personnel."""
    source = _lire("static/js/patients.js")
    assert "maybe_printed" in _corps_fonction(source, "postPrintConfirmation")
    assert "maybe_printed" in _corps_fonction(source, "enqueuePrintConfirmation")
    corps = _corps_fonction(source, "handlePrintConfirmation")
    assert "case 'uncertain'" in corps
    assert "ticket_received" in corps
    assert "ticket_missing" in corps
    assert "callStaffFlow" in corps
    incertain = corps[corps.index("case 'uncertain'"):corps.index("case 'ask'")]
    assert "uncertainAbandon" in incertain
    assert "abandon_timer" in incertain
    # Le garde-fou incertain ramène à l'accueil sans annuler : le ticket est
    # peut-être sorti.
    assert "abandonFlow" not in incertain
    assert "goHome" in incertain


def test_abandon_ne_laisse_pas_les_boutons_de_reessai_actifs():
    """Pendant le POST d'abandon, l'écran ne doit plus permettre un clic
    « Réessayer » qui imprimerait un ticket pour une inscription annulée."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "abandonFlow")
    assert "renderPrintOverlay('', [])" in corps
    assert "data.status === 'activated'" in corps
    assert "conclusionTimer().goHome" in corps


def test_refresh_ne_detruit_pas_un_flux_d_impression():
    """Un refresh Socket.IO pendant impression/décision ne doit pas remplacer
    le fragment de conclusion par les boutons d'accueil."""
    source = _lire("static/js/patients.js")
    assert "printFlowActive()" in _corps_fonction(source, "refresh_buttons")
    assert "printFlowActive()" in _corps_fonction(source, "refresh_page")
    conclusion = _corps_fonction(
        _lire("static/js/patient_conclusion.js"), "goToCancelPatient")
    assert "finishActivePrintFlow" in conclusion
    reimpression = _corps_fonction(
        _lire("static/js/patient_conclusion.js"), "handlePrintButtonClick")
    assert "_activePrintJobId" in reimpression
    assert "finishActivePrintFlow" in reimpression


def test_file_persistee_en_localstorage_avec_repli():
    """La file survit au rechargement/redémarrage (localStorage), avec un
    repli mémoire si le stockage est indisponible."""
    source = _lire("static/js/patients.js")
    assert "localStorage" in source
    assert "PRINT_QUEUE_KEY" in source
    assert "_memPrintQueue" in source


def test_requetes_d_impression_sont_bornees_en_temps():
    """Aucun POST critique ne doit suspendre l'écran indéfiniment : un
    AbortController borne confirmation, appel personnel et abandon."""
    source = _lire("static/js/patients.js")
    assert "PRINT_REQUEST_TIMEOUT_MS" in source
    assert "AbortController" in _corps_fonction(source, "fetchJsonWithTimeout")
    assert "fetchJsonWithTimeout" in _corps_fonction(source, "postPrintConfirmation")
    assert "fetchJsonWithTimeout" in _corps_fonction(source, "postPrintCallStaff")
    assert "fetchJsonWithTimeout" in _corps_fonction(source, "postPrintAbandon")


def test_5xx_reste_en_file():
    """Un 5xx laisse l'état serveur inconnu : le job doit être retenté."""
    corps = _corps_fonction(_lire("static/js/patients.js"), "postPrintConfirmation")
    assert "r.status >= 500" in corps


def test_reponse_ancienne_ne_retire_pas_une_decision_plus_recente():
    """Un POST en vol pour une ancienne version du résultat ne doit pas
    retirer la décision réenfilée ensuite sous le même print_job_id."""
    source = _lire("static/js/patients.js")
    corps = _corps_fonction(source, "dequeuePrintConfirmation")
    assert "queueId" in corps
    assert "j.queueId !== queueId" in corps
    assert "queueId" in _corps_fonction(source, "enqueuePrintConfirmation")
    assert "hasNewerQueuedConfirmation" in source


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


def _pending_patient(application, print_job_id="job-1", journey_id=None):
    """Crée une inscription EN ATTENTE d'acquittement d'impression."""
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O",
                            notification=False, specific_message="")
        patient = Patient(call_number="A1", status="pending",
                          activity=activite, language=langue,
                          print_job_id=print_job_id, journey_id=journey_id)
        db.session.add_all([langue, activite, patient])
        db.session.commit()
        return patient.id


def test_pending_concurrent_du_parcours_reutilise_l_existant(application, monkeypatch):
    """Si la contrainte journey_id dénonce une insertion concurrente, le
    perdant récupère le pending et son print_job_id au lieu d'échouer en 500."""
    import python.engine as engine
    from sqlalchemy.exc import IntegrityError

    pid = _pending_patient(application, print_job_id="job-gagnant", journey_id="j-race")
    monkeypatch.setattr(engine, "get_next_call_number", lambda activity: "A2")
    monkeypatch.setattr(
        engine, "add_patient",
        lambda *a, **kw: (_ for _ in ()).throw(
            IntegrityError("INSERT", {}, Exception("duplicate journey"))))

    with application.app_context():
        activite = Activity.query.first()
        patient = engine.register_pending_patient(
            activite, "job-perdant", journey_id="j-race")
        assert patient.id == pid
        assert patient.print_job_id == "job-gagnant"


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


def test_busy_ne_declenche_jamais_cancel_meme_si_un_client_le_poste(client, application):
    """Défense serveur : 'busy' n'est pas un résultat matériel. Même un
    ancien client qui le posterait ne doit pas annuler le pending."""
    application.config["PAGE_PATIENT_PRINT_FAIL_BEHAVIOR"] = "cancel"
    _pending_patient(application)

    r = client.post("/patient/confirm_print", json={
        "print_job_id": "job-1", "success": False, "code": "busy"})

    assert r.get_json()["status"] == "uncertain"
    with application.app_context():
        assert Patient.query.filter_by(print_job_id="job-1").one().status == "pending"


def test_resultat_incertain_ne_peut_pas_annuler_en_mode_cancel(client, application):
    """Une erreur après envoi possible au ticket ne déclenche jamais le mode
    'cancel' : l'inscription reste pending tant que le patient n'a pas tranché."""
    application.config["PAGE_PATIENT_PRINT_FAIL_BEHAVIOR"] = "cancel"
    _pending_patient(application)

    r = client.post("/patient/confirm_print", json={
        "print_job_id": "job-1", "success": False,
        "code": "error_print", "maybe_printed": True})

    assert r.status_code == 200
    donnees = r.get_json()
    assert donnees["status"] == "uncertain"
    assert donnees["abandon_timer"] == 60
    with application.app_context():
        assert Patient.query.filter_by(print_job_id="job-1").one().status == "pending"


def test_resultat_incertain_puis_ticket_recu_active(client, application):
    """Le patient confirme visuellement le ticket : l'inscription pending
    entre alors en file, sans second tirage physique."""
    _pending_patient(application)

    r1 = client.post("/patient/confirm_print", json={
        "print_job_id": "job-1", "success": False, "maybe_printed": True})
    assert r1.get_json()["status"] == "uncertain"

    r2 = client.post("/patient/confirm_print", json={
        "print_job_id": "job-1", "success": True,
        "code": "ticket_received"})
    assert r2.get_json()["status"] == "activated"
    with application.app_context():
        assert Patient.query.filter_by(print_job_id="job-1").one().status == "standing"


def test_resultat_incertain_puis_absence_confirmee_applique_cancel(client, application):
    """Après observation « pas de ticket », le résultat redevient certain :
    le comportement d'échec configuré peut alors annuler l'inscription."""
    application.config["PAGE_PATIENT_PRINT_FAIL_BEHAVIOR"] = "cancel"
    _pending_patient(application)

    client.post("/patient/confirm_print", json={
        "print_job_id": "job-1", "success": False, "maybe_printed": True})
    r = client.post("/patient/confirm_print", json={
        "print_job_id": "job-1", "success": False,
        "code": "not_printed", "maybe_printed": False})

    assert r.get_json()["status"] == "cancelled"
    with application.app_context():
        assert Patient.query.filter_by(print_job_id="job-1").one().status == "print_failed"


def test_abandon_qui_perd_la_course_renvoie_l_etat_reel(client, application):
    """Si confirm_print a activé avant print_abandon, ce dernier ne doit pas
    mentir 'cancelled' : il renvoie l'état métier réellement tranché."""
    _pending_patient(application)
    client.post("/patient/confirm_print",
                json={"print_job_id": "job-1", "success": True})

    r = client.post("/patient/print_abandon", json={"print_job_id": "job-1"})

    assert r.status_code == 200
    assert r.get_json()["status"] == "activated"
    with application.app_context():
        assert Patient.query.filter_by(print_job_id="job-1").one().status == "standing"


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


def test_expiration_utilise_un_update_conditionnel():
    """L'expiration ne doit pas pouvoir écraser un patient activé entre le
    SELECT des pendings et l'UPDATE : le statut attendu reste dans le WHERE."""
    corps = _corps_fonction(_lire("python/engine.py"), "expire_stale_pending_patients")
    assert ".update(" in corps
    assert "Patient.status == 'pending'" in corps


def test_expiration_marque_au_lieu_de_supprimer(application):
    """Le TTL ne supprime plus la ligne : 'expired' garde la trace du travail
    d'impression pour la réconciliation, et libère le parcours."""
    from python.engine import expire_stale_pending_patients
    from config import time_tz

    pid = _pending_patient(application)
    with application.app_context():
        patient = db.session.get(Patient, pid)
        patient.journey_id = "j-ttl"
        patient.timestamp = datetime.now(time_tz) - timedelta(seconds=9999)
        db.session.commit()

        assert expire_stale_pending_patients() == 1
        patient = db.session.get(Patient, pid)
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


def test_resultat_incertain_tardif_laisse_le_patient_trancher(client, application):
    """Même après expiration du pending, un résultat ambigu n'est pas tranché
    automatiquement : « ticket reçu » réconcilie, « absent » laisse expiré."""
    _patient_expire(application)

    r = client.post("/patient/confirm_print", json={
        "print_job_id": "job-x", "success": False, "maybe_printed": True})

    donnees = r.get_json()
    assert r.status_code == 200
    assert donnees["status"] == "uncertain"
    with application.app_context():
        assert Patient.query.filter_by(print_job_id="job-x").one().status == "expired"

    r2 = client.post("/patient/confirm_print", json={
        "print_job_id": "job-x", "success": True, "code": "ticket_received"})
    assert r2.get_json()["status"] == "activated"
    with application.app_context():
        assert Patient.query.filter_by(print_job_id="job-x").one().status == "standing"


def test_appel_personnel_sur_inscription_expiree_est_confirme(client, application):
    """Si le patient demande le personnel depuis un résultat incertain déjà
    expiré, la demande est réellement signalée et la borne peut l'annoncer."""
    _patient_expire(application)

    r = client.post("/patient/print_call_staff", json={"print_job_id": "job-x"})

    donnees = r.get_json()
    assert r.status_code == 200
    assert donnees["status"] == "expired"
    assert donnees["staff_called"] is True


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
