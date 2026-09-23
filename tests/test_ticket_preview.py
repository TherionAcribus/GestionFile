"""Apercu Admin du ticket, fonde sur le meme flux ESC/POS que l'impression."""

import os
from types import SimpleNamespace

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from utils import (
    convert_markdown_to_escpos,
    escpos_to_preview_lines,
    render_ticket_escpos,
)

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


@pytest.fixture
def application():
    app = Flask(__name__, template_folder=os.path.join(_SERVEUR, "templates"))
    app.config.update(
        SECRET_KEY="secret-test-ticket-preview",
        TESTING=True,
        PHARMACY_NAME="Pharmacie de test",
    )
    return app


def test_interprete_les_styles_escpos_pour_l_apercu():
    escpos = convert_markdown_to_escpos(
        "[center][double]**N° A-12**[/double][/center]\n__Merci__",
        line_width=42,
    )

    lines = escpos_to_preview_lines(escpos)

    assert lines[0]["alignment"] == "center"
    assert lines[0]["double"] is True
    assert lines[0]["segments"][0] == {
        "text": "N° A-12",
        "double": True,
        "bold": True,
        "underline": False,
    }
    assert lines[1]["segments"][0]["text"] == "Merci"
    assert lines[1]["segments"][0]["underline"] is True


def test_apercu_et_impression_partagent_le_rendu(application):
    patient = SimpleNamespace(call_number="B-7", activity_id=None, activity=None)
    with application.app_context():
        rendered = render_ticket_escpos(
            ["Bienvenue à la {P}", "[center]Numéro {N}[/center]"],
            patient,
            line_width=48,
            language_code="fr",
        )

    assert "Pharmacie de test" in rendered
    assert "B-7" in rendered
    assert "\x1b\x61\x01" in rendered


def test_route_affiche_les_valeurs_non_enregistrees(application, monkeypatch):
    from routes import admin_patient

    activity = SimpleNamespace(id=3, name="Ordonnances", specific_message="")
    patient = SimpleNamespace(call_number="C-9", activity_id=3, activity=activity)
    fake_db = SimpleNamespace(
        session=SimpleNamespace(get=lambda model, identifier: activity)
    )
    monkeypatch.setattr(admin_patient, "db", fake_db)
    monkeypatch.setattr(admin_patient, "get_futur_patient",
                        lambda call_number, selected: patient)

    with application.test_request_context(
        "/admin/patient/ticket_preview",
        method="POST",
        data={
            "ticket_header": "[center]Nouvel en-tête[/center]",
            "ticket_message": "[double]**Numéro {N}**[/double]",
            "ticket_footer": "Merci",
            "printer_width": "48",
            "display_specific_message": "false",
            "call_number": "C-9",
            "activity": "3",
            "language": "fr",
        },
    ):
        html = admin_patient.ticket_preview.__wrapped__()

    assert "Nouvel en-tête" in html
    assert "C-9" in html
    assert "ticket-preview-align-center" in html
    assert "ticket-preview-double" in html
    assert "Modifications en cours" in html
    assert 'ticket-preview-align-center"><span' in html


def test_route_echappe_le_texte_admin(application, monkeypatch):
    from routes import admin_patient

    activity = SimpleNamespace(id=1, name="Test", specific_message="")
    fake_db = SimpleNamespace(
        session=SimpleNamespace(get=lambda model, identifier: activity)
    )
    monkeypatch.setattr(admin_patient, "db", fake_db)
    monkeypatch.setattr(
        admin_patient,
        "get_futur_patient",
        lambda call_number, selected: SimpleNamespace(
            call_number=call_number, activity_id=None, activity=selected),
    )

    with application.test_request_context(
        "/admin/patient/ticket_preview",
        method="POST",
        data={
            "ticket_header": "<script>alert(1)</script>",
            "ticket_message": "Numéro {N}",
            "ticket_footer": "",
            "printer_width": "42",
            "call_number": "A-1",
            "activity": "1",
            "language": "fr",
        },
    ):
        html = admin_patient.ticket_preview.__wrapped__()

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_route_signale_un_balisage_invalide(application, monkeypatch):
    from routes import admin_patient

    activity = SimpleNamespace(id=1, name="Test", specific_message="")
    monkeypatch.setattr(
        admin_patient,
        "db",
        SimpleNamespace(session=SimpleNamespace(
            get=lambda model, identifier: activity)),
    )

    with application.test_request_context(
        "/admin/patient/ticket_preview",
        method="POST",
        data={
            "ticket_header": "[center]Non fermé",
            "ticket_message": "Numéro {N}",
            "ticket_footer": "",
            "printer_width": "42",
            "call_number": "A-1",
            "activity": "1",
            "language": "fr",
        },
    ):
        html = admin_patient.ticket_preview.__wrapped__()

    assert "Aperçu indisponible" in html
    assert "Balisage non fermé" in html


def test_page_charge_le_script_et_evite_l_id_qrcode_duplique():
    patient_page = open(
        os.path.join(_SERVEUR, "templates", "admin", "patient_page.html"),
        encoding="utf-8",
    ).read()
    ticket_page = open(
        os.path.join(_SERVEUR, "templates", "admin", "page_patient_ticket.html"),
        encoding="utf-8",
    ).read()

    assert "admin_ticket_preview.js" in patient_page
    assert 'id="ticket_preview_result"' in ticket_page
    assert 'id="ticket-test-form"' in ticket_page
    assert 'id="qrcode-form"' not in ticket_page
