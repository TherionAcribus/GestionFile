"""UUID de parcours QR : salle Socket.IO dédiée par borne.

Avant : le scan d'un QR téléphone émettait ``update_scan_phone`` à TOUTES les
bornes du namespace ``/socket_patient``, et chaque borne affichant un QR
réagissait avec son propre numéro « futur ». Avec plusieurs bornes ou
parcours simultanés, une borne pouvait afficher la confirmation d'un scan qui
ne la concernait pas.

Désormais chaque parcours QR porte un UUID : encodé dans l'URL du QR, rendu en
``data-journey-id``, et sert de salle ``scan_<uuid>`` que la borne rejoint via
``join_scan_journey``. ``/patient/phone/ping`` n'émet ``update_scan_phone``
que dans cette salle, avec le numéro RÉELLEMENT attribué à l'inscription.

Verrouillé ici :

1. ``create_qr_code`` encode le journey dans l'URL et dans le nom de fichier
   (deux bornes au même numéro « futur » ne s'écrasent plus leur QR) ;
2. ``join_scan_journey`` fait rejoindre la salle, seule destinataire de
   l'évènement ; les salles précédentes sont quittées ;
3. ``/patient/phone/ping`` émet dans la salle du parcours — et ne diffuse plus
   à toutes les bornes quand le journey est absent (QR antérieur) ;
4. ``_qr_image_for_conclusion`` retrouve le fichier par journey même quand le
   numéro réel diffère du numéro « futur ».
"""

import os
from types import SimpleNamespace

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from models import db, Activity, Language, Patient

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def application(tmp_path):
    app = Flask(
        __name__,
        template_folder=os.path.join(_SERVEUR, "templates"),
        static_folder=str(tmp_path),
    )
    app.config.update(
        SECRET_KEY="secret-test-scan-journey",
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # db.metadatas est partagé entre fichiers de test : le bind 'users'
        # peut déjà y être déclaré (cf. test_phone_patient_token).
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
        PAGE_PATIENT_QRCODE_WEB_PAGE=True,
        SERVER_URL="http://borne.test/",
        # Clés lues par /patient/phone/ping pour le gabarit de confirmation.
        PHONE_LINE1="{N}",
        PHONE_LINE2="",
        PHONE_LINE3="",
        PHONE_LINE4="",
        PHONE_LINE5="",
        PHONE_LINE6="",
        PHONE_DISPLAY_SPECIFIC_MESSAGE=False,
        PHONE_CENTER=True,
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


def _activite(application, letter="O"):
    """Crée langue + activité, renvoie l'id de l'activité."""
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter=letter,
                            notification=False, specific_message="")
        db.session.add_all([langue, activite])
        db.session.commit()
        return activite.id


# --- 1. create_qr_code --------------------------------------------------------

def test_qr_code_encode_le_journey(application, monkeypatch):
    """L'URL scannée porte ?journey=<uuid> et le fichier est unique par
    parcours (deux bornes au même numéro « futur » n'écrivent plus le même
    fichier)."""
    from python import engine

    captured = {}

    class _Img:
        def save(self, path):
            captured["path"] = path

    def _make(data):
        captured["data"] = data
        return _Img()

    monkeypatch.setattr(engine.qrcode, "make", _make)

    patient = SimpleNamespace(id=None, call_number="A1",
                              activity=SimpleNamespace(id=3))
    with application.test_request_context("/patient"):
        from flask import session
        session["language_code"] = "fr"
        filename = engine.create_qr_code(patient, journey_id="j-123")

    assert captured["data"] == "http://borne.test//patient/phone/fr/A1/3?journey=j-123"
    assert filename == "qr_patient-A1-j-123.png"


def test_qr_code_sans_journey_reste_compatible(application, monkeypatch):
    """Sans journey (ex. QR de test admin), l'URL et le nom de fichier sont
    inchangés — compat ascendante."""
    from python import engine

    captured = {}

    class _Img:
        def save(self, path):
            pass

    def _make(data):
        captured["data"] = data
        return _Img()

    monkeypatch.setattr(engine.qrcode, "make", _make)

    patient = SimpleNamespace(id=None, call_number="A1",
                              activity=SimpleNamespace(id=3))
    with application.test_request_context("/patient"):
        from flask import session
        session["language_code"] = "fr"
        filename = engine.create_qr_code(patient)

    assert captured["data"] == "http://borne.test//patient/phone/fr/A1/3"
    assert filename == "qr_patient-A1.png"


# --- 2. La salle scan_<journey> sur /socket_patient ---------------------------

def _connecte_borne(application, client):
    from extensions import socketio
    import sockets  # noqa: F401 — enregistre les handlers sur l'objet partagé

    return socketio.test_client(
        application, flask_test_client=client, namespace="/socket_patient")


def test_update_scan_phone_cible_la_salle_du_parcours(client, application):
    """Seule la borne ayant rejoint scan_<journey> reçoit l'évènement ; une
    autre borne (autre parcours, ou aucun) ne reçoit rien."""
    from extensions import socketio
    import sockets  # noqa: F401
    from communication import communikation

    socketio.init_app(application)
    sio_a = _connecte_borne(application, client)
    sio_b = _connecte_borne(application, client)
    assert sio_a.is_connected("/socket_patient")
    assert sio_b.is_connected("/socket_patient")

    sio_a.emit("join_scan_journey", {"journey": "parcours-1"},
               namespace="/socket_patient")
    sio_b.emit("join_scan_journey", {"journey": "parcours-2"},
               namespace="/socket_patient")

    with application.app_context():
        communikation("patient", event="update_scan_phone",
                      data={"call_number": "A1"},
                      room=sockets.scan_journey_room("parcours-1"))

    recus_a = [m for m in sio_a.get_received("/socket_patient")
               if m["name"] == "update_scan_phone"]
    assert len(recus_a) == 1
    assert recus_a[0]["args"][0]["data"] == {"call_number": "A1"}
    assert all(m["name"] != "update_scan_phone"
               for m in sio_b.get_received("/socket_patient"))


def test_join_scan_journey_quitte_les_salles_precedentes(client, application):
    """Rejoindre un nouveau parcours quitte la salle précédente : la borne ne
    peut plus recevoir l'évènement de l'ancien QR."""
    from extensions import socketio
    import sockets  # noqa: F401
    from communication import communikation

    socketio.init_app(application)
    sio = _connecte_borne(application, client)
    sio.emit("join_scan_journey", {"journey": "ancien"}, namespace="/socket_patient")
    sio.emit("join_scan_journey", {"journey": "nouveau"}, namespace="/socket_patient")

    with application.app_context():
        communikation("patient", event="update_scan_phone",
                      data={"call_number": "A1"},
                      room=sockets.scan_journey_room("ancien"))

    assert all(m["name"] != "update_scan_phone"
               for m in sio.get_received("/socket_patient"))


def test_join_scan_journey_rejette_les_ids_bizarres(client, application):
    """Un journey invalide ne fait rejoindre aucune salle."""
    from extensions import socketio
    import sockets  # noqa: F401
    from communication import communikation

    socketio.init_app(application)
    sio = _connecte_borne(application, client)
    sio.emit("join_scan_journey", {"journey": "../autre_salle"},
               namespace="/socket_patient")
    sio.emit("join_scan_journey", {"journey": None}, namespace="/socket_patient")
    sio.emit("join_scan_journey", None, namespace="/socket_patient")

    with application.app_context():
        communikation("patient", event="update_scan_phone",
                      data={"call_number": "A1"},
                      room=sockets.scan_journey_room("../autre_salle"))

    assert all(m["name"] != "update_scan_phone"
               for m in sio.get_received("/socket_patient"))


# --- 3. /patient/phone/ping ---------------------------------------------------

def test_ping_emet_dans_la_salle_du_parcours(client, application, monkeypatch):
    """Le ping d'une NOUVELLE inscription émet update_scan_phone dans
    scan_<journey>, avec le numéro réellement attribué."""
    import routes.patient as patient_module

    appels = []
    monkeypatch.setattr(
        patient_module, "communikation",
        lambda *args, **kwargs: appels.append(kwargs))

    activite_id = _activite(application)
    client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id),
        "language_code": "fr",
        "journey": "j-9",
    })

    scans = [kw for kw in appels if kw.get("event") == "update_scan_phone"]
    assert len(scans) == 1
    assert scans[0]["room"] == "scan_j-9"

    with application.app_context():
        patient = Patient.query.filter_by(activity_id=activite_id).one()
    assert scans[0]["data"] == {"call_number": patient.call_number}


def test_ping_sans_journey_ne_diffuse_pas(client, application, monkeypatch):
    """QR antérieur (pas de journey) : l'inscription a toujours lieu, mais
    aucune diffusion — une confirmation ne doit jamais atterrir sur le mauvais
    écran."""
    import routes.patient as patient_module

    appels = []
    monkeypatch.setattr(
        patient_module, "communikation",
        lambda *args, **kwargs: appels.append(kwargs))

    activite_id = _activite(application)
    reponse = client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id),
        "language_code": "fr",
    })

    assert reponse.status_code == 200
    assert not any(kw.get("event") == "update_scan_phone" for kw in appels)
    with application.app_context():
        assert Patient.query.filter_by(activity_id=activite_id).count() == 1


# --- 4. _qr_image_for_conclusion ----------------------------------------------

def test_qr_image_conclusion_retrouve_par_journey(application):
    """Le numéro réel (A6) diffère du « futur » (A5) : la recherche par
    suffixe journey retrouve quand même le bon fichier."""
    from routes.patient import _qr_image_for_conclusion

    qr_dir = os.path.join(application.static_folder, "qr_patients")
    os.makedirs(qr_dir)
    open(os.path.join(qr_dir, "qr_patient-A5-j1.png"), "w").close()

    with application.app_context():
        assert _qr_image_for_conclusion("A6", "j1") == "qr_patient-A5-j1.png"


def test_qr_image_conclusion_repli_par_numero(application):
    """Sans journey (accès direct à l'URL) : dernier fichier du numéro, puis
    nom historique si rien n'existe."""
    from routes.patient import _qr_image_for_conclusion

    qr_dir = os.path.join(application.static_folder, "qr_patients")
    os.makedirs(qr_dir)
    open(os.path.join(qr_dir, "qr_patient-A5-jx.png"), "w").close()

    with application.app_context():
        assert _qr_image_for_conclusion("A5", None) == "qr_patient-A5-jx.png"
        assert _qr_image_for_conclusion("B9", None) == "qr_patient-B9.png"
