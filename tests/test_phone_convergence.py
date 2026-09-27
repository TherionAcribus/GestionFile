"""Convergence du téléphone patient sur les clôtures de parcours.

Avant : seul le statut ``calling`` était exploité par le téléphone — un
patient servi (``done``) ou retiré (``cancelled``) pendant une coupure
Socket.IO (ou simplement connecté : aucun évènement de clôture n'était
émis vers la salle ``call_<n>``) restait indéfiniment sur « en file » ou
« votre tour ».

Désormais :

1. ``notify_patient_phone_closed`` émet ``refresh`` dans la salle
   ``call_<numéro>`` — le client recharge et ``/patient/phone/ping`` rend
   l'état réel ;
2. tous les chemins de clôture y passent : ``validate_current``, ``pause``,
   le balayage de ``call_specific`` et de ``call_next``, les actions App
   (``validate``/``delete``), la correction/suppression admin, l'expiration
   des inscriptions ``pending`` et les annulations d'impression ;
3. ``phone_patient_ping`` rend le fragment « parcours terminé » pour un
   patient terminal (cookie rejoué, re-scan du QR, lien de suivi), et le
   fragment « lien invalide » si la ligne a été purgée ;
4. ``phone.js`` recharge au reconnect quand le statut est terminal — sans
   boucle (repère ``data-journey-state="closed"`` du fragment).
"""

import os
from datetime import datetime, timedelta

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, Counter, Language, Patient, Pharmacist

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def application(tmp_path):
    app = Flask(
        __name__,
        template_folder=os.path.join(_SERVEUR, "templates"),
        static_folder=str(tmp_path),
    )
    app.config.update(
        SECRET_KEY="secret-test-phone-convergence",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
        PHONE_TITLE="Suivi",
        PHONE_LINE1="{N}",
        PHONE_LINE2="",
        PHONE_LINE3="",
        PHONE_LINE4="",
        PHONE_LINE5="",
        PHONE_LINE6="",
        PHONE_DISPLAY_SPECIFIC_MESSAGE=False,
        PHONE_CENTER=True,
        PHONE_JOURNEY_END_MESSAGE="Passage terminé",
        PAGE_PATIENT_DISABLE_DEFAULT_MESSAGE="Activité indisponible",
        PAGE_PATIENT_PRINT_FAIL_BEHAVIOR="cancel",
        ANNOUNCE_CALL_TEXT="Patient {N} au comptoir {C}",
        ALGO_IS_ACTIVATED=False,
        ALGO_OVERTAKEN_LIMIT=10,
        PHARMACY_NAME="Pharmacie de test",
    )
    db.init_app(app)
    from routes.patient import patient_bp
    from routes.counter import counter_bp
    app.register_blueprint(patient_bp)
    app.register_blueprint(counter_bp)

    @app.context_processor
    def _layout_helpers():
        return dict(get_css_url=lambda mode=None: "",
                    page_layout_style=lambda *a, **k: "")

    with app.app_context():
        db.create_all()
        yield app


@pytest.fixture
def client(application):
    return application.test_client()


@pytest.fixture(autouse=True)
def _silence_diffusion(monkeypatch):
    """Neutralise la diffusion temps réel non ciblée par les tests."""
    import routes.patient as patient_module
    import routes.counter as counter_module
    import python.engine as engine
    import services.calling_service as service

    noop = lambda *a, **k: None  # noqa: E731
    for module in (patient_module, counter_module, engine, service):
        monkeypatch.setattr(module, "communikation", noop, raising=False)
        monkeypatch.setattr(module, "send_app_notification", noop, raising=False)
        monkeypatch.setattr(module, "notify_patient_phone", noop, raising=False)
        monkeypatch.setattr(module, "trigger_async_audio_calling", noop,
                            raising=False)


def _base(application):
    """Langue + activité (+ comptoir), renvoie (activity_id, counter_id)."""
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter="O")
        comptoir = Counter(name="Comptoir 1", sort_order=1)
        db.session.add_all([langue, activite, comptoir])
        db.session.commit()
        return activite.id, comptoir.id


def _patient(application, call_number="A12", status="standing",
             counter_id=None, journey_id=None, print_job_id=None,
             timestamp=None):
    with application.app_context():
        langue = Language.query.first()
        activite = Activity.query.first()
        p = Patient(call_number=call_number, status=status,
                    activity_id=activite.id, language_id=langue.id,
                    counter_id=counter_id, journey_id=journey_id,
                    print_job_id=print_job_id, timestamp=timestamp)
        db.session.add(p)
        db.session.commit()
        return p.id


def _cookies_signes(client, application, patient_id, call_number):
    from auth_utils import make_patient_phone_token
    with application.app_context():
        token = make_patient_phone_token(patient_id, call_number)
    client.set_cookie('patient_id', str(patient_id))
    client.set_cookie('patient_call_number', call_number)
    client.set_cookie('patient_token', token)


# --- 1. ping : fragment « parcours terminé » ---------------------------------

@pytest.mark.parametrize("statut", ["done", "cancelled", "expired", "print_failed"])
def test_ping_cookie_patient_terminal_rend_le_fragment_termine(
        client, application, statut):
    """Rechargement / ping rejoué : un parcours clos ne réaffiche plus
    l'écran « en file » — le fragment porte le repère anti-boucle lu par
    phone.js."""
    activite_id, _ = _base(application)
    pid = _patient(application, status=statut)
    _cookies_signes(client, application, pid, "A12")

    reponse = client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr"})

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert 'data-journey-state="closed"' in html
    assert "Passage terminé" in html


def test_ping_journey_terminal_rend_le_fragment_termine(client, application):
    """Re-scan du MÊME QR après clôture : le parcours dédupliqué est
    terminal — pas de confirmation « inscrit »."""
    activite_id, _ = _base(application)
    _patient(application, status="done", journey_id="j-clos")

    reponse = client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr",
        "journey": "j-clos"})

    html = reponse.get_data(as_text=True)
    assert 'data-journey-state="closed"' in html
    assert "Passage terminé" in html


def test_ping_ticket_patient_terminal_rend_le_fragment_termine(
        client, application):
    """Le lien de suivi signé d'un passage terminé rend « terminé », pas la
    confirmation d'inscription."""
    from auth_utils import make_patient_phone_token

    activite_id, _ = _base(application)
    pid = _patient(application, status="done")
    with application.app_context():
        ticket = make_patient_phone_token(pid, "A12")

    reponse = client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr",
        "ticket": ticket})

    html = reponse.get_data(as_text=True)
    assert 'data-journey-state="closed"' in html


def test_ping_cookie_sur_ligne_purgee_rend_le_lien_invalide(client, application):
    """La ligne a été purgée (fin de journée) mais le cookie vit encore :
    même écran qu'un lien de suivi mort."""
    activite_id, _ = _base(application)
    pid = _patient(application, status="done")
    _cookies_signes(client, application, pid, "A12")
    with application.app_context():
        db.session.delete(db.session.get(Patient, pid))
        db.session.commit()

    reponse = client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr"})

    html = reponse.get_data(as_text=True)
    assert "lien de suivi est invalide" in html
    assert 'data-journey-state="closed"' not in html


def test_ping_patient_en_file_rend_toujours_la_confirmation(client, application):
    """Non-régression : un patient vivant reçoit sa confirmation normale."""
    activite_id, _ = _base(application)
    pid = _patient(application, status="standing")
    _cookies_signes(client, application, pid, "A12")

    reponse = client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr"})

    html = reponse.get_data(as_text=True)
    assert 'data-journey-state="closed"' not in html


# --- 2. notify_patient_phone_closed : salle et évènement ----------------------

def test_cloture_emet_refresh_dans_la_salle_du_patient(client, application):
    """Le téléphone signé et joint à call_A12 reçoit 'refresh' ; un autre
    téléphone (autre salle) ne reçoit rien."""
    from extensions import socketio
    import sockets  # noqa: F401 — enregistre les handlers
    from communication import notify_patient_phone_closed

    socketio.init_app(application)

    autre_client = application.test_client()
    _cookies_signes(client, application, 5, "A12")
    _cookies_signes(autre_client, application, 6, "B99")
    sio_a = socketio.test_client(application, flask_test_client=client,
                                 namespace='/socket_phone')
    sio_b = socketio.test_client(application, flask_test_client=autre_client,
                                 namespace='/socket_phone')
    assert sio_a.is_connected('/socket_phone')
    assert sio_b.is_connected('/socket_phone')

    with application.app_context():
        notify_patient_phone_closed("A12")

    assert any(m['name'] == 'refresh' for m in sio_a.get_received('/socket_phone'))
    assert all(m['name'] != 'refresh' for m in sio_b.get_received('/socket_phone'))


def test_cloture_sans_numero_n_emet_pas(application, monkeypatch):
    """Patient sans numéro d'appel : aucun emit (salle indéfinie)."""
    import communication

    emis = []
    monkeypatch.setattr(communication.socketio, "emit",
                        lambda *a, **k: emis.append((a, k)))
    assert communication.notify_patient_phone_closed(None) is False
    assert communication.notify_patient_phone_closed("") is False
    assert emis == []


# --- 3. Les chemins de clôture notifient le téléphone -------------------------

def _capture_clotures(monkeypatch, module):
    captures = []
    monkeypatch.setattr(module, "notify_patient_phone_closed",
                        lambda number: captures.append(number))
    return captures


def test_validate_current_notifie_les_patients_clotures(application, monkeypatch):
    import services.calling_service as service
    captures = _capture_clotures(monkeypatch, service)

    _, comptoir_id = _base(application)
    _patient(application, call_number="A12", status="calling",
             counter_id=comptoir_id)
    _patient(application, call_number="A13", status="ongoing",
             counter_id=comptoir_id)

    with application.app_context():
        service.validate_current(comptoir_id)

    assert sorted(captures) == ["A12", "A13"]


def test_pause_notifie_le_patient_cloture(application, monkeypatch):
    import services.calling_service as service
    captures = _capture_clotures(monkeypatch, service)

    _, comptoir_id = _base(application)
    pid = _patient(application, call_number="A12", status="calling",
                   counter_id=comptoir_id)

    with application.app_context():
        service.pause(comptoir_id, pid)

    assert captures == ["A12"]


def test_call_specific_notifie_les_patients_balayes(application, monkeypatch):
    """« Terminer puis appeler » : le patient en cours clôturé par le
    balayage notifie son téléphone, pas seulement l'écran d'annonce."""
    import services.calling_service as service
    captures = _capture_clotures(monkeypatch, service)
    monkeypatch.setattr(service, "trigger_async_audio_calling",
                        lambda *a, **k: None)
    monkeypatch.setattr(service, "notify_patient_phone", lambda *a, **k: None)

    _, comptoir_id = _base(application)
    with application.app_context():
        membre = Pharmacist(name="Membre", initials="M1")
        membre.activities.append(Activity.query.first())
        comptoir = db.session.get(Counter, comptoir_id)
        comptoir.staff = membre
        db.session.add(membre)
        db.session.commit()
    _patient(application, call_number="A12", status="ongoing",
             counter_id=comptoir_id)
    cible = _patient(application, call_number="A13", status="standing")

    with application.app_context():
        ok, _payload, statut = service.call_specific(comptoir_id, cible)

    assert ok and statut == 200
    assert captures == ["A12"]


def test_call_next_balayage_notifie(application, monkeypatch):
    """Le balayage des 'calling' périmés par call_next notifie aussi."""
    import python.engine as engine
    import services.calling_service as service
    captures = _capture_clotures(monkeypatch, engine)
    monkeypatch.setattr(service, "notify_patient_phone", lambda *a, **k: None)

    _, comptoir_id = _base(application)
    with application.app_context():
        membre = Pharmacist(name="Membre", initials="M1")
        membre.activities.append(Activity.query.first())
        comptoir = db.session.get(Counter, comptoir_id)
        comptoir.staff = membre
        db.session.add(membre)
        db.session.commit()
    _patient(application, call_number="A12", status="calling",
             counter_id=comptoir_id)

    with application.app_context():
        engine.call_next(comptoir_id)

    assert "A12" in captures


def test_expire_pending_notifie(application, monkeypatch):
    """Un téléphone suivant une inscription 'pending' (scan pendant
    l'attente d'impression) est prévenu quand elle expire."""
    import python.engine as engine
    captures = _capture_clotures(monkeypatch, engine)

    _base(application)
    _patient(application, status="pending", print_job_id="job-1",
             timestamp=datetime.now() - timedelta(minutes=30))

    with application.app_context():
        expirees = engine.expire_stale_pending_patients(ttl_seconds=60)

    assert expirees == 1
    assert captures == ["A12"]


def test_app_validate_et_delete_notifient(client, application, monkeypatch):
    """Actions App : 'validate' (servi) et 'delete' (retiré) préviennent le
    téléphone du patient."""
    import routes.counter as counter_module
    from auth_utils import generate_app_token
    captures = _capture_clotures(monkeypatch, counter_module)

    _base(application)
    servi = _patient(application, call_number="A12", status="ongoing")
    retire = _patient(application, call_number="A13", status="standing")
    with application.app_context():
        token = generate_app_token()

    client.post(f"/api/counter/validate_patient/{servi}",
                headers={"X-App-Token": token})
    client.post(f"/api/counter/delete_patient/{retire}",
                headers={"X-App-Token": token})

    assert captures == ["A12", "A13"]


def test_app_renvoi_en_file_ne_notifie_pas_de_cloture(
        client, application, monkeypatch):
    """'standing' n'est PAS une clôture : le parcours continue, le
    téléphone doit garder son écran."""
    import routes.counter as counter_module
    from auth_utils import generate_app_token
    captures = _capture_clotures(monkeypatch, counter_module)

    _, comptoir_id = _base(application)
    pid = _patient(application, status="calling", counter_id=comptoir_id)
    with application.app_context():
        token = generate_app_token()

    reponse = client.post(f"/api/counter/put_standing_list/{pid}",
                          headers={"X-App-Token": token})

    assert reponse.status_code == 201
    assert captures == []


def test_confirm_print_cancel_notifie(client, application, monkeypatch):
    """Échec d'impression en mode 'cancel' : un téléphone qui suivait le
    parcours pending est prévenu de la clôture."""
    import routes.patient as patient_module
    captures = _capture_clotures(monkeypatch, patient_module)

    _base(application)
    _patient(application, status="pending", print_job_id="job-9")

    reponse = client.post("/patient/confirm_print", json={
        "print_job_id": "job-9", "success": False})

    assert reponse.status_code == 200
    assert captures == ["A12"]


def test_print_abandon_notifie(client, application, monkeypatch):
    import routes.patient as patient_module
    captures = _capture_clotures(monkeypatch, patient_module)

    _base(application)
    _patient(application, status="pending", print_job_id="job-10")

    reponse = client.post("/patient/print_abandon", json={
        "print_job_id": "job-10"})

    assert reponse.status_code == 200
    assert captures == ["A12"]


# --- 4. Gardes statiques -------------------------------------------------------

def test_les_clotures_admin_notifient_aussi():
    """Correction manuelle (statut terminal) et suppression admin : les deux
    chemins notifient le téléphone — garde statique."""
    import inspect
    import routes.admin_queue as admin_queue

    source = inspect.getsource(admin_queue.update_patient)
    assert "notify_patient_phone_closed" in source
    source = inspect.getsource(admin_queue.delete_patient)
    assert "notify_patient_phone_closed" in source


def test_phone_js_traite_les_statuts_terminaux_au_reconnect():
    """Le relevé de statut au connect gère les quatre issues terminales et
    s'appuie sur le repère du fragment pour ne pas boucler."""
    js_path = os.path.join(_SERVEUR, "static", "js", "phone.js")
    with open(js_path, encoding="utf-8") as f:
        js = f.read()

    assert "phoneSocket.on('connect'" in js
    for statut in ("'done'", "'cancelled'", "'expired'", "'print_failed'"):
        assert statut in js
    assert "data-journey-state" in js
    assert "window.location.reload()" in js
