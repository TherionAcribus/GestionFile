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

1. ``qr_code_data_uri`` encode le journey dans l'URL et renvoie le PNG en
   ``data:`` URI — aucun fichier n'est écrit : un call_number réutilisé
   (autre jour, autre borne) ne peut plus servir un QR périmé depuis le
   cache navigateur ;
2. ``join_scan_journey`` fait rejoindre la salle, seule destinataire de
   l'évènement ; les salles précédentes sont quittées ;
3. ``/patient/phone/ping`` émet dans la salle du parcours — et ne diffuse plus
   à toutes les bornes quand le journey est absent (QR antérieur) ;
4. la page de conclusion génère son QR à la volée pour le patient RÉEL —
   y compris en impression directe, où aucun QR de validation n'existait.
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
        PHONE_TITLE="Suivi",
        PHONE_LINE1="{N}",
        PHONE_LINE2="",
        PHONE_LINE3="",
        PHONE_LINE4="",
        PHONE_LINE5="",
        PHONE_LINE6="",
        PHONE_DISPLAY_SPECIFIC_MESSAGE=False,
        PHONE_CENTER=True,
        # Clés lues par patient_conclusion_page (page de conclusion).
        PAGE_PATIENT_CONFIRMATION_MESSAGE="Ticket {N} - {A}",
        PAGE_PATIENT_END_TIMER=10,
        PAGE_PATIENT_QRCODE_DISPLAY=True,
        PAGE_PATIENT_PRINT_AFTER_PRINT=False,
        PAGE_PATIENT_PRINT_AFTER_SCAN=False,
        PAGE_PATIENT_INTERFACE_PRINTING="impression",
        PAGE_PATIENT_INTERFACE_PRINT_FAILED="echec",
        PAGE_PATIENT_INTERFACE_RETRY="retry",
        PAGE_PATIENT_INTERFACE_CALL_STAFF="staff",
        PAGE_PATIENT_INTERFACE_STAFF_CALLED="appele",
        PAGE_PATIENT_INTERFACE_NO_TICKET="pas de ticket",
        PAGE_PATIENT_INTERFACE_PRINT_FAILED_STAFF="voir personnel",
        PAGE_PATIENT_INTERFACE_DONE_BACK="retour",
        PAGE_PATIENT_INTERFACE_DONE_PRINT="imprimer",
        PAGE_PATIENT_INTERFACE_DONE_EXTEND="prolonger",
        # Clés lues par format_ticket_text (chemin print_and_validate) :
        # Markdown brut, converti en ESC/POS à l'impression (PRINTER_WIDTH).
        TICKET_DISPLAY_SPECIFIC_MESSAGE=False,
        TICKET_HEADER="",
        TICKET_MESSAGE="{N}",
        TICKET_FOOTER="",
        PRINTER_WIDTH=48,
    )
    db.init_app(app)
    from routes.patient import patient_bp
    app.register_blueprint(patient_bp)
    # Gabarits rendus hors de app.py : les helpers de layout/css y sont
    # injectés par des context processors — on les neutralise pour le test.
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


def _activite(application, letter="O"):
    """Crée langue + activité, renvoie l'id de l'activité."""
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        activite = Activity(name="Ordonnance", letter=letter,
                            notification=False, specific_message="")
        db.session.add_all([langue, activite])
        db.session.commit()
        return activite.id


# --- 1. qr_code_data_uri ------------------------------------------------------

def test_qr_code_encode_le_journey(application, monkeypatch):
    """L'URL scannée porte ?journey=<uuid> ; l'image est une data URI —
    aucun fichier n'est écrit, donc aucun risque de cache périmé ou de
    collision de nom entre parcours au même numéro."""
    from python import engine

    captured = {}

    class _Img:
        def save(self, buf, format=None):
            captured["format"] = format
            buf.write(b'PNGDATA')

    def _make(data):
        captured["data"] = data
        return _Img()

    monkeypatch.setattr(engine.qrcode, "make", _make)

    patient = SimpleNamespace(id=None, call_number="A1",
                              activity=SimpleNamespace(id=3))
    with application.test_request_context("/patient"):
        from flask import session
        session["language_code"] = "fr"
        uri = engine.qr_code_data_uri(patient, journey_id="j-123")

    assert captured["data"] == "http://borne.test//patient/phone/fr/A1/3?journey=j-123"
    assert captured["format"] == "PNG"
    assert uri == "data:image/png;base64,UE5HREFUQQ=="


def test_qr_code_sans_journey_reste_compatible(application, monkeypatch):
    """Sans journey (ex. QR de test admin, QR de conclusion), l'URL ne porte
    pas de paramètre — compat ascendante du contenu encodé."""
    from python import engine

    captured = {}

    class _Img:
        def save(self, buf, format=None):
            buf.write(b'PNGDATA')

    def _make(data):
        captured["data"] = data
        return _Img()

    monkeypatch.setattr(engine.qrcode, "make", _make)

    patient = SimpleNamespace(id=None, call_number="A1",
                              activity=SimpleNamespace(id=3))
    with application.test_request_context("/patient"):
        from flask import session
        session["language_code"] = "fr"
        uri = engine.qr_code_data_uri(patient)

    assert captured["data"] == "http://borne.test//patient/phone/fr/A1/3"
    assert uri.startswith("data:image/png;base64,")


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
    assert scans[0]["data"] == {"call_number": patient.call_number,
                                "patient_id": patient.id}


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


# --- 4. QR de conclusion : généré à la volée pour le patient réel -------------

def test_conclusion_sert_une_data_uri_pas_un_fichier(client, application):
    """Le QR de conclusion est embarqué en data URI : plus de fichier
    ``qr_patient-<numéro>.png`` que le cache navigateur pourrait servir
    périmé (autre jour, autre langue ou activité sous la même URL)."""
    _vieux_id, nouveau_id = _deux_patients_meme_numero(application)

    reponse = client.get(f"/patient/conclusion_page/{nouveau_id}")

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "data:image/png;base64," in html
    assert "qr_patients/" not in html


def test_conclusion_qr_encode_le_patient_reel(client, application, monkeypatch):
    """Le QR de conclusion encode le numéro/activité du patient réellement
    inscrit (et non un fichier lié au numéro « futur » du parcours)."""
    from python import engine

    captured = []

    class _Img:
        def save(self, buf, format=None):
            buf.write(b'PNGDATA')

    monkeypatch.setattr(engine.qrcode, "make", lambda data: captured.append(data) or _Img())

    _vieux_id, nouveau_id = _deux_patients_meme_numero(application)
    with application.app_context():
        activite_id = Patient.query.get(nouveau_id).activity_id

    client.get(f"/patient/conclusion_page/{nouveau_id}")

    # Le QR de conclusion porte désormais un lien de suivi signé (?ticket=)
    # vers le passage existant — le scanner ne doit pas créer d'inscription.
    assert len(captured) == 1
    url, _, ticket = captured[0].partition("?ticket=")
    assert url == f"http://borne.test//patient/phone/fr/A5/{activite_id}"
    assert ticket

    # Le jeton encodé se résout bien vers le patient réel.
    from auth_utils import patient_ticket_patient_id
    with application.app_context():
        assert patient_ticket_patient_id(ticket) == nouveau_id


def test_conclusion_impression_directe_a_un_qr(client, application):
    """Impression directe : la conclusion a bien un QR alors que ce chemin
    ne passait pas par la page de validation (aucun QR n'était généré)."""
    activite_id = _activite(application)
    with client.session_transaction() as sess:
        sess["language_code"] = "fr"

    reponse = client.post("/patient/print_and_validate",
                          data={"activity_id": str(activite_id)},
                          headers={"HX-Request": "true"})

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "data:image/png;base64," in html


# --- 5. Conclusion : identité par patient_id, pas par call_number -------------

def _deux_patients_meme_numero(application):
    """Un call_number est réutilisé d'un jour à l'autre : un ANCIEN patient
    (statut done) et le NOUVEAU (standing) partagent 'A5'. Le nom d'activité
    ({A}) distingue lequel est rendu par la page de conclusion."""
    with application.app_context():
        langue = Language(code="fr", name="Français", translation="Français")
        ancienne = Activity(name="ANCIENNE", letter="Z")
        nouvelle = Activity(name="NOUVELLE", letter="Z")
        vieux = Patient(call_number="A5", status="done",
                        activity=ancienne, language=langue)
        nouveau = Patient(call_number="A5", status="standing",
                          activity=nouvelle, language=langue)
        db.session.add_all([langue, ancienne, nouvelle, vieux, nouveau])
        db.session.commit()
        return vieux.id, nouveau.id


def test_conclusion_par_patient_id_rend_le_bon_patient(client, application):
    """La conclusion identifie le patient par son id : même call_number qu'un
    patient d'hier → c'est le NOUVEAU qui est affiché."""
    _vieux_id, nouveau_id = _deux_patients_meme_numero(application)

    reponse = client.post("/patient/scan_already_validate",
                          data={"patient_id": str(nouveau_id),
                                "journey": "j-x"})

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "NOUVELLE" in html
    assert "ANCIENNE" not in html


# --- 6. Un parcours = une inscription (déduplication serveur) -----------------

def test_ping_meme_journey_ne_cree_pas_de_doublon(client, application):
    """Deux téléphones sans cookie scannent le MÊME QR : une seule
    inscription, et le second ping retrouve le patient du parcours."""
    activite_id = _activite(application)

    for telephone in (client, application.test_client()):
        reponse = telephone.post("/patient/phone/ping", data={
            "activity_id": str(activite_id),
            "language_code": "fr",
            "journey": "j-dedup",
        })
        assert reponse.status_code == 200

    with application.app_context():
        patients = Patient.query.filter_by(journey_id="j-dedup").all()
        assert len(patients) == 1


def test_ping_rejoue_remet_le_meme_patient_aux_cookies(client, application):
    """Rejeu du ping (même journey, cookies expirés) : pas de nouvelle
    inscription, les cookies sont re-posés sur le patient du parcours."""
    activite_id = _activite(application)
    client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr",
        "journey": "j-rejeu"})
    with application.app_context():
        patient_id = Patient.query.filter_by(journey_id="j-rejeu").one().id

    autre = application.test_client()
    reponse = autre.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr",
        "journey": "j-rejeu"})

    assert reponse.status_code == 200
    assert f"patient_id={patient_id}" in str(reponse.headers.getlist("Set-Cookie"))
    with application.app_context():
        assert Patient.query.count() == 1


def test_bouton_scan_reutilise_le_patient_du_parcours(client, application):
    """Scan du QR par téléphone PUIS clic « scan » sur la borne (même
    journey) : aucun second patient n'est créé."""
    activite_id = _activite(application)
    client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr",
        "journey": "j-scan-btn"})

    with client.session_transaction() as sess:
        sess["language_code"] = "fr"
    client.post("/patient/scan_and_validate", data={
        "activity_id": str(activite_id), "journey": "j-scan-btn"})

    with application.app_context():
        assert Patient.query.filter_by(journey_id="j-scan-btn").count() == 1


def test_impression_recliquee_reutilise_l_inscription_pending(client, application):
    """Deux clics « Imprimer » sur le même parcours : un seul patient
    pending, et le même print_job_id (confirm_print reste idempotent)."""
    activite_id = _activite(application)
    with client.session_transaction() as sess:
        sess["language_code"] = "fr"

    for _ in range(2):
        reponse = client.post("/patient/print_and_validate", data={
            "activity_id": str(activite_id), "journey": "j-print"},
            headers={"HX-Request": "true"})
        assert reponse.status_code == 200

    with application.app_context():
        patients = Patient.query.filter_by(journey_id="j-print").all()
        assert len(patients) == 1
        assert patients[0].status == "pending"
        assert patients[0].print_job_id


def test_scan_apres_impression_active_le_pending(client, application):
    """Impression initiée puis choix « scan » sur le même parcours : le
    pending entre en file (sans ticket), sans doublon."""
    activite_id = _activite(application)
    with client.session_transaction() as sess:
        sess["language_code"] = "fr"

    client.post("/patient/print_and_validate", data={
        "activity_id": str(activite_id), "journey": "j-mix"},
        headers={"HX-Request": "true"})
    client.post("/patient/scan_and_validate", data={
        "activity_id": str(activite_id), "journey": "j-mix"},
        headers={"HX-Request": "true"})

    with application.app_context():
        patients = Patient.query.filter_by(journey_id="j-mix").all()
        assert len(patients) == 1
        assert patients[0].status == "standing"


def test_ping_sur_pending_du_parcours_l_active(client, application):
    """QR scanné APRÈS un clic « Imprimer » (pending existant, même
    journey) : le pending est activé, aucun doublon."""
    activite_id = _activite(application)
    with client.session_transaction() as sess:
        sess["language_code"] = "fr"
    client.post("/patient/print_and_validate", data={
        "activity_id": str(activite_id), "journey": "j-pending"},
        headers={"HX-Request": "true"})

    telephone = application.test_client()
    telephone.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr",
        "journey": "j-pending"})

    with application.app_context():
        patients = Patient.query.filter_by(journey_id="j-pending").all()
        assert len(patients) == 1
        assert patients[0].status == "standing"


# --- 7. QR de conclusion : lien de suivi, pas d'inscription ------------------

def test_ticket_de_conclusion_suit_le_passage_sans_reinscrire(client, application):
    """Ping avec le ticket signé du QR de conclusion : affiche le passage
    existant (cookies posés) sans créer de patient."""
    from auth_utils import make_patient_phone_token

    _vieux_id, nouveau_id = _deux_patients_meme_numero(application)
    with application.app_context():
        patient = Patient.query.get(nouveau_id)
        ticket = make_patient_phone_token(patient.id, patient.call_number)
        activite_id = patient.activity_id

    telephone = application.test_client()
    reponse = telephone.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr",
        "ticket": ticket})

    assert reponse.status_code == 200
    assert f"patient_id={nouveau_id}" in str(reponse.headers.getlist("Set-Cookie"))
    with application.app_context():
        assert Patient.query.count() == 2  # inchangé : pas de nouvelle ligne


def test_ticket_invalide_ne_cree_aucune_inscription(client, application):
    """Ticket falsifié ou dont le passage a été purgé : message d'erreur,
    jamais d'inscription."""
    activite_id = _activite(application)

    reponse = client.post("/patient/phone/ping", data={
        "activity_id": str(activite_id), "language_code": "fr",
        "ticket": "ticket.falsifie"})
    assert "invalide" in reponse.get_data(as_text=True)

    with application.app_context():
        assert Patient.query.count() == 0


def test_page_telephone_avec_ticket_invalide_affiche_une_erreur(client, application):
    """GET /patient/phone avec un ticket ne menant à aucun patient : page
    d'erreur au lieu d'un écran d'attente qui ne réussira jamais."""
    reponse = client.get("/patient/phone/fr/A1/1?ticket=ticket.falsifie")
    assert reponse.status_code == 200
    assert "invalide" in reponse.get_data(as_text=True)


def test_conclusion_repli_call_number_prend_le_plus_recent(client, application):
    """Repli compat (pas de patient_id dans le POST) : parmi les patients au
    même numéro, on prend le PLUS RÉCENT — pas le premier trouvé."""
    _deux_patients_meme_numero(application)

    reponse = client.post("/patient/scan_already_validate",
                          data={"patient_call_number": "A5"})

    html = reponse.get_data(as_text=True)
    assert "NOUVELLE" in html
    assert "ANCIENNE" not in html


def test_conclusion_page_route_par_patient_id(client, application):
    """L'accès direct à /patient/conclusion_page/<id> rend le bon patient."""
    _vieux_id, nouveau_id = _deux_patients_meme_numero(application)

    reponse = client.get(f"/patient/conclusion_page/{nouveau_id}")

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    assert "NOUVELLE" in html
    assert "ANCIENNE" not in html


# --- 6. Libellés d'impression : {N} réservé à la borne ------------------------

def test_conclusion_print_labels_gardent_n_resolvent_les_autres_balises(
        client, application):
    """``print_ui_labels`` : {P} (et les autres balises « avant appel ») sont
    résolus côté serveur, mais {N} reste littéral — patients.js le remplace par
    le numéro dans un élément mis en valeur (.print_status_number)."""
    import json
    import re

    application.config["PHARMACY_NAME"] = "Pharmacie Test"
    application.config["PAGE_PATIENT_INTERFACE_PRINT_FAILED"] = \
        "Impression impossible à {P}. Votre numéro est le {N}."
    _vieux_id, nouveau_id = _deux_patients_meme_numero(application)

    reponse = client.get(f"/patient/conclusion_page/{nouveau_id}")

    html = reponse.get_data(as_text=True)
    assert reponse.status_code == 200
    match = re.search(
        r'<script id="print_ui_labels" type="application/json">(.*?)</script>',
        html, re.S)
    labels = json.loads(match.group(1))
    assert labels["print_failed"] == \
        "Impression impossible à Pharmacie Test. Votre numéro est le {N}."
