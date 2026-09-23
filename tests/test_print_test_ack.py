"""Acquittement des tirages de test (point : impression de test muette).

Avant : ``print_ticket_test`` et ``print_test_ticket_size`` repondaient 204
des l'emission Socket.IO — l'admin ignorait si la borne etait connectee,
approvisionnee, ou si l'impression avait reussi.

Maintenant :

1. La route genere (ou reprend) un ``job_id`` et le place dans le champ
   ``flag`` de l'enveloppe ``print_ticket`` ; la reponse porte un
   ``HX-Trigger`` ``print_test_sent``.
2. La borne (patients.js) renvoie le resultat du pont d'impression via
   ``print_test_result`` sur /socket_patient ; sockets.py le relaie tel quel
   sur /socket_admin.
3. admin.js affiche l'etat (envoye / imprime / indisponible / papier /
   aucun retour) dans ``#print_test_result`` du gabarit ticket.
"""

import json
import os
import re

import pytest
from flask import Flask

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as f:
        return f.read()


def _func_body(source, func):
    m = re.search(r"def " + func + r"\(.*?\n(.*?)(?=\n@|\ndef |\Z)", source,
                  re.DOTALL)
    assert m, f"fonction {func} introuvable"
    return m.group(1)


@pytest.fixture
def application():
    app = Flask(__name__, template_folder=os.path.join(_SERVEUR, "templates"))
    app.config["SECRET_KEY"] = "secret-test-print-ack"
    return app


# --- Route : job_id + flag + HX-Trigger -------------------------------------

def test_job_id_fourni_par_le_client_est_reutilise(application):
    from routes.admin_patient import _test_print_job_id
    with application.test_request_context(
            "/admin/patient/print_ticket_test", method="POST",
            data={"job_id": "job-42"}):
        assert _test_print_job_id() == "job-42"


def test_job_id_invalide_ou_absent_est_regenere(application):
    from routes.admin_patient import _test_print_job_id
    with application.test_request_context(
            "/admin/patient/print_ticket_test", method="POST",
            data={"job_id": "../../evil<script>"}):
        job_id = _test_print_job_id()
    assert re.match(r"^[0-9a-f]{32}$", job_id)
    with application.test_request_context("/admin/patient/print_ticket_test"):
        assert re.match(r"^[0-9a-f]{32}$", _test_print_job_id())


def test_reponse_204_porte_le_hx_trigger(application):
    from routes.admin_patient import _test_print_sent_response
    with application.test_request_context("/"):
        resp = _test_print_sent_response("job-9")
    assert resp.status_code == 204
    trigger = json.loads(resp.headers["HX-Trigger"])
    assert trigger["print_test_sent"]["job_id"] == "job-9"


def test_routes_emettent_le_job_id_dans_flag():
    source = _read("routes/admin_patient.py")
    for func in ("print_ticket_test", "print_ticket_test_size"):
        body = _func_body(source, func)
        assert "flag=job_id" in body, \
            f"{func} doit emettre print_ticket avec flag=job_id"
        assert "_test_print_sent_response(job_id)" in body


def test_routes_vraiment_applees(application, monkeypatch):
    """La vue (hors decorateur de permission) propage le job_id du client."""
    from routes import admin_patient

    emissions = []
    # admin_patient a lie communikation a son import : c'est ce nom-la qu'il
    # faut patcher (et non communication.communikation).
    monkeypatch.setattr(admin_patient, "communikation",
                        lambda *a, **kw: emissions.append((a, kw)))

    with application.test_request_context(
            "/admin/patient/print_ticket_test",
            method="POST",
            data={"call_number": "A-1", "activity": "1",
                  "language": "fr", "job_id": "job-live"}):
        # Les vues lisent session / Activity : on court-circuite les acces
        # metier pour n'eprouver que la correlation.
        monkeypatch.setattr(admin_patient, "Activity",
                            type("A", (), {"query": type(
                                "Q", (), {"get": staticmethod(lambda i: None)})()}))
        monkeypatch.setattr(admin_patient, "get_futur_patient",
                            lambda cn, act: None)
        monkeypatch.setattr(admin_patient, "format_ticket_text",
                            lambda p, a: "QkFTRTY0")
        from flask import session
        session["language_code"] = "fr"   # langue admin avant l'essai
        resp = admin_patient.print_ticket_test.__wrapped__()
        # La langue demandee pour l'essai (form: fr ici) est restauree.
        assert session["language_code"] == "fr"

    assert resp.status_code == 204
    assert len(emissions) == 1
    assert emissions[0][1]["flag"] == "job-live"
    assert emissions[0][1]["event"] == "print_ticket"


def test_langue_session_restauree_apres_essai_etranger(application, monkeypatch):
    """Un essai en espagnol ne change pas durablement la langue admin :
    la valeur precedente est restauree, y compris son ABSENCE."""
    from routes import admin_patient
    from flask import session

    monkeypatch.setattr(admin_patient, "Activity",
                        type("A", (), {"query": type(
                            "Q", (), {"get": staticmethod(lambda i: None)})()}))
    monkeypatch.setattr(admin_patient, "get_futur_patient", lambda c, a: None)
    monkeypatch.setattr(admin_patient, "format_ticket_text", lambda p, a: "WA==")
    monkeypatch.setattr(admin_patient, "communikation", lambda *a, **k: None)

    ctx = dict(method="POST",
               data={"call_number": "A-1", "activity": "1",
                     "language": "es"})

    # Session deja en allemand -> on retrouve l'allemand apres l'essai.
    with application.test_request_context("/admin/patient/print_ticket_test",
                                          **ctx):
        session["language_code"] = "de"
        admin_patient.print_ticket_test.__wrapped__()
        assert session["language_code"] == "de"

    # Pas de langue en session -> la clef est retiree, pas posee.
    with application.test_request_context("/admin/patient/print_ticket_test",
                                          **ctx):
        admin_patient.print_ticket_test.__wrapped__()
        assert "language_code" not in session


def test_route_qr_restaure_la_langue(application, monkeypatch):
    """La route de test QR remettait systematiquement 'fr' : elle doit
    maintenant restaurer la valeur precedente comme print_ticket_test."""
    from routes import admin_patient
    from flask import session

    monkeypatch.setattr(admin_patient, "Activity",
                        type("A", (), {"query": type(
                            "Q", (), {"get": staticmethod(lambda i: None)})()}))
    monkeypatch.setattr(admin_patient, "get_futur_patient", lambda c, a: None)
    monkeypatch.setattr(admin_patient, "qr_code_data_uri", lambda p: "data:x")
    monkeypatch.setattr(admin_patient, "render_template",
                        lambda *a, **k: "ok")

    with application.test_request_context(
            "/admin/patient/qr_code/test?language=es"):
        session["language_code"] = "de"
        admin_patient.admin_patient_qr_code_modal.__wrapped__()
        assert session["language_code"] == "de"


# --- Relais socket borne -> admin -------------------------------------------

def test_relais_print_test_result(monkeypatch):
    import communication
    import sockets

    relays = []
    monkeypatch.setattr(communication, "communikation",
                        lambda *a, **kw: relays.append((a, kw)))

    sockets.print_test_result({
        "job_id": "job-1", "success": True, "code": "print_ok",
        "message": "ok", "borne_id": "borne-7",
    })

    assert len(relays) == 1
    args, kwargs = relays[0]
    assert args[0] == "admin"
    assert kwargs["event"] == "print_test_result"
    data = kwargs["data"]
    assert data["job_id"] == "job-1"
    assert data["success"] is True
    assert data["borne_id"] == "borne-7"


def test_relais_rejette_les_payloads_bizarres(monkeypatch):
    import communication
    import sockets

    relays = []
    monkeypatch.setattr(communication, "communikation",
                        lambda *a, **kw: relays.append((a, kw)))

    sockets.print_test_result(None)
    sockets.print_test_result("chaine")
    sockets.print_test_result({"job_id": "../evil"})
    sockets.print_test_result({"success": True})   # job_id absent
    assert relays == []


def test_relais_tronque_les_champs(monkeypatch):
    import communication
    import sockets

    relays = []
    monkeypatch.setattr(communication, "communikation",
                        lambda *a, **kw: relays.append((a, kw)))

    sockets.print_test_result({
        "job_id": "job-1", "success": 1,
        "code": "x" * 200, "message": "m" * 1000,
        "borne_id": "b" * 200,
    })
    data = relays[0][1]["data"]
    assert len(data["code"]) <= 64
    assert len(data["message"]) <= 300
    assert len(data["borne_id"]) <= 80
    assert data["success"] is True


# --- Gardes statiques borne / admin ------------------------------------------

def test_routes_de_test_restaurent_la_langue_session():
    """Les deux routes d'essai sauvegardent puis restaurent
    ``session['language_code']`` (try/finally) — pas de valeur codee en dur."""
    source = _read("routes/admin_patient.py")
    for func in ("print_ticket_test", "admin_patient_qr_code_modal"):
        body = _func_body(source, func)
        assert "previous_language" in body
        assert "finally" in body
        assert 'session["language_code"] = "fr"' not in body, \
            f"{func} impose le francais au lieu de restaurer la session"


def test_patients_js_acquitte_le_resultat():
    js = _read("static/js/patients.js")
    m = re.search(r"on\('print_ticket',\s*function\(msg\)\s*{(.*?)\n\s*}\);",
                  js, re.DOTALL)
    assert m, "handler print_ticket introuvable"
    body = m.group(1)
    assert "msg.flag" in body
    assert "emit('print_test_result'" in body
    assert "borne_id" in body


def test_admin_js_ecoute_l_acquittement():
    js = _read("static/js/admin.js")
    assert "'print_test_result'" in js
    assert "data-print-test" in js
    assert "PRINT_TEST_TIMEOUT_MS" in js


def test_gabarit_ticket_expose_la_zone_de_retour():
    html = _read("templates/admin/page_patient_ticket.html")
    assert 'id="print_test_result"' in html
    assert html.count("data-print-test") >= 2
    assert "<button" in html   # le declencheur de test est un vrai <button>


def test_aucune_balise_btn_factice_dans_les_gabarits():
    """``<btn>`` n'existe pas en HTML : les navigateurs le traitent en
    element inconnu (pas de clavier, pas de focus correct). Deux occurrences
    ont ete corrigees (page_patient_ticket.html, translations_tab_texts.html)
    — ce test empeche le retour de la faute de frappe."""
    import glob
    for path in glob.glob(os.path.join(_SERVEUR, "templates", "**", "*.html"),
                          recursive=True):
        rel = os.path.relpath(path, _SERVEUR)
        assert not re.search(r"<btn[\s>]", _read(rel)), rel
