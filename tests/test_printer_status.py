"""Supervision des statuts imprimante (lot A08).

Verrouillé ici :

- les statuts métier identiques mettent à jour la même ligne au lieu de
  remplir ``printer_status`` à chaque contrôle périodique ;
- les heartbeats prouvent que la borne est en ligne sans devenir l'état
  imprimante affiché ;
- l'erreur est calculée par borne : une borne en erreur n'est plus masquée
  par le dernier statut OK d'une autre borne ;
- une borne sans contact récent devient explicitement injoignable dans
  l'historique et dans le drapeau global d'erreur.
"""

import os
from datetime import datetime, timedelta

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from config import time_tz
from models import (
    KIOSK_OFFLINE_CODE,
    PRINTER_HEARTBEAT_CODE,
    PRINTER_STATUS_STALE_SECONDS,
    PrinterStatus,
    db,
    get_printer_error,
    get_printer_infos,
    record_printer_status,
)


@pytest.fixture
def application():
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        # db.metadatas est partagé entre fichiers de test : un autre module
        # peut déjà y avoir enregistré le bind 'users'.
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        TESTING=True,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app


def _count():
    return PrinterStatus.query.count()


def test_identical_status_updates_same_row(application):
    with application.app_context():
        assert record_printer_status(
            "error_not_found", "Imprimante absente", borne_id="borne-1") is True
        assert record_printer_status(
            "error_not_found", "Imprimante absente", borne_id="borne-1") is False

        rows = PrinterStatus.query.all()
        assert len(rows) == 1
        assert rows[0].error_code == "error_not_found"
        assert get_printer_error() is True


def test_heartbeat_updates_contact_without_becoming_displayed_status(application):
    with application.app_context():
        assert record_printer_status(
            "init_ok", "Imprimante initialisée", borne_id="borne-1") is True
        assert record_printer_status(
            PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-1") is True
        assert record_printer_status(
            PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-1") is False

        rows = PrinterStatus.query.all()
        assert len(rows) == 2
        heartbeats = [row for row in rows
                      if row.error_code == PRINTER_HEARTBEAT_CODE]
        assert len(heartbeats) == 1

        infos = get_printer_infos()
        assert [info["error_code"] for info in infos] == ["init_ok"]
        assert get_printer_error() is False


def test_error_on_one_borne_is_not_masked_by_ok_on_another(application):
    with application.app_context():
        record_printer_status(
            "error_print", "Erreur imprimante", borne_id="borne-1")
        record_printer_status(
            "paper_ok", "Papier OK", borne_id="borne-2")

        # Avant : le dernier enregistrement global était « paper_ok » et
        # l'erreur de borne-1 disparaissait du tableau de bord.
        assert get_printer_error() is True


def test_stale_borne_is_reported_offline(application):
    with application.app_context():
        record_printer_status(
            "init_ok", "Imprimante initialisée", borne_id="borne-1")
        record_printer_status(
            PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-1")

        stale_at = datetime.now(time_tz) - timedelta(
            seconds=PRINTER_STATUS_STALE_SECONDS + 30)
        PrinterStatus.query.update({"received_at": stale_at})
        db.session.commit()

        assert get_printer_error() is True
        offline = [info for info in get_printer_infos()
                   if info["error_code"] == KIOSK_OFFLINE_CODE]
        assert len(offline) == 1
        assert offline[0]["borne_id"] == "borne-1"
        assert "injoignable" in offline[0]["message"]


def test_fresh_heartbeat_keeps_old_paper_state_current(application):
    with application.app_context():
        record_printer_status(
            "no_paper", "Plus de papier", borne_id="borne-1")
        record_printer_status(
            PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-1")

        infos = get_printer_infos()
        assert [info["error_code"] for info in infos] == ["no_paper"]
        assert get_printer_error() is False


def test_latest_contact_uses_received_at_not_row_id(application):
    with application.app_context():
        # Le heartbeat est inséré AVANT le statut métier : sa ligne conserve
        # donc un petit id même lorsqu'elle est rafraîchie ensuite.
        record_printer_status(
            PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-1")
        record_printer_status(
            "init_ok", "Imprimante initialisée", borne_id="borne-1")

        stale_at = datetime.now(time_tz) - timedelta(
            seconds=PRINTER_STATUS_STALE_SECONDS + 30)
        db.session.query(PrinterStatus).filter_by(error_code="init_ok").update(
            {"received_at": stale_at})
        db.session.commit()
        record_printer_status(
            PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-1")

        assert get_printer_error() is False


def test_heartbeat_survives_business_status_purge(application):
    with application.app_context():
        record_printer_status(
            PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-1")
        for index in range(12):
            record_printer_status(
                f"state_{index}", f"État {index}", borne_id="borne-1")

        # Sans filtre de purge sur les heartbeats, la ligne initiale serait
        # devenue la plus ancienne par id puis aurait disparu, faisant passer
        # la borne pour injoignable alors qu'elle vient d'émettre.
        assert db.session.query(PrinterStatus).filter_by(
            error_code=PRINTER_HEARTBEAT_CODE).one().borne_id == "borne-1"
        assert get_printer_error() is False


def test_server_only_statuses_keep_last_state_semantics(application):
    with application.app_context():
        record_printer_status("error_print", "Échec serveur")
        record_printer_status("init_ok", "Nouveau statut serveur")

        # Pas de borne_id : comportement historique, dernier statut gagne.
        assert get_printer_error() is False
        assert _count() == 2
