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
    PRINTER_STATUS_KEEP,
    PRINTER_STATUS_STALE_SECONDS,
    PrinterStatus,
    borne_overview,
    borne_printing_available,
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


def test_borne_reachability_tracks_last_contact(application):
    with application.app_context():
        from models import borne_reachability
        assert borne_reachability() == {}

        record_printer_status(
            PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-1")
        record_printer_status(
            PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-2")
        assert borne_reachability() == {"borne-1": True, "borne-2": True}

        stale_at = datetime.now(time_tz) - timedelta(
            seconds=PRINTER_STATUS_STALE_SECONDS + 30)
        db.session.query(PrinterStatus).filter_by(
            borne_id="borne-1").update({"received_at": stale_at})
        db.session.commit()

        assert borne_reachability() == {"borne-1": False, "borne-2": True}


def test_borne_printing_available_per_borne(application):
    with application.app_context():
        from models import borne_printing_available
        # Aucun statut connu : pas de raison d'alarmer.
        assert borne_printing_available("borne-inconnue") is True

        record_printer_status(
            "init_ok", "Imprimante initialisée", borne_id="borne-1")
        assert borne_printing_available("borne-1") is True

        record_printer_status(
            "no_paper", "Plus de papier", borne_id="borne-1")
        assert borne_printing_available("borne-1") is False

        record_printer_status(
            "paper_ok", "Papier OK", borne_id="borne-1")
        assert borne_printing_available("borne-1") is True

        # low_paper laisse imprimer : l'équipe est alertée, pas le patient.
        record_printer_status(
            "low_paper", "Papier bientôt épuisé", borne_id="borne-1")
        assert borne_printing_available("borne-1") is True

        # Un code d'erreur bloque ; l'état est par borne, pas global.
        record_printer_status(
            "error_print", "Échec impression", borne_id="borne-1")
        assert borne_printing_available("borne-1") is False
        assert borne_printing_available("borne-2") is True


def test_borne_overview_reports_per_borne_state(application):
    """La vue consolidée par borne distingue joignabilité et état imprimante,
    indépendamment l'une de l'autre."""
    with application.app_context():
        record_printer_status("paper_ok", "OK", borne_id="borne-1")
        record_printer_status("no_paper", "Plus de papier", borne_id="borne-2")

        overview = {b["borne_id"]: b for b in borne_overview()}

        assert overview["borne-1"]["reachable"] is True
        assert overview["borne-1"]["code"] == "paper_ok"
        assert overview["borne-1"]["is_error"] is False
        assert overview["borne-2"]["reachable"] is True
        assert overview["borne-2"]["code"] == "no_paper"
        assert overview["borne-2"]["message"] == "Plus de papier"


def test_business_journal_keeps_printer_status_keep_rows(application):
    """La purge conserve ``PRINTER_STATUS_KEEP`` statuts métier : le journal
    de la page Bornes dépasse les 10 lignes de la carte dashboard."""
    with application.app_context():
        for i in range(PRINTER_STATUS_KEEP + 5):
            record_printer_status(
                "error_print", f"Échec {i}", borne_id="borne-1")

        kept = PrinterStatus.query.filter(
            PrinterStatus.error_code != "heartbeat").count()
        assert kept == PRINTER_STATUS_KEEP


def test_kiosk_event_does_not_mask_printer_state(application):
    """Un incident borne (kiosk_*) rafraîchit le contact mais ne devient pas
    le statut métier : no_paper continue de bloquer l'impression."""
    with application.app_context():
        record_printer_status("no_paper", "Plus de papier", borne_id="borne-1")
        record_printer_status("kiosk_page_dead", "Page figée, rechargée",
                              borne_id="borne-1", is_error=True)

        overview = {b["borne_id"]: b for b in borne_overview()}
        assert overview["borne-1"]["code"] == "no_paper"
        assert overview["borne-1"]["reachable"] is True
        assert borne_printing_available("borne-1") is False

        infos = get_printer_infos()
        kiosk_rows = [i for i in infos if i.get("error_code") == "kiosk_page_dead"]
        assert len(kiosk_rows) == 1
        assert kiosk_rows[0]["error"] is True


def test_kiosk_event_only_borne_stays_printable(application):
    """Une borne n'ayant remonté que des incidents n'est pas signalée en
    erreur imprimante : l'évènement n'est pas un état métier."""
    with application.app_context():
        record_printer_status("kiosk_session_fail", "Ticket refusé",
                              borne_id="borne-1", is_error=True)
        assert borne_printing_available("borne-1") is True
        assert get_printer_error() is False


def test_borne_id_vide_normalise_en_null(application):
    """Une chaîne vide n'est pas un identifiant : normalisée en NULL à
    l'enregistrement, elle ne peut plus créer de borne fantôme « » dans
    les snapshots."""
    from models import _borne_status_snapshots
    with application.app_context():
        record_printer_status("paper_ok", "Papier OK", borne_id="")
        row = PrinterStatus.query.order_by(PrinterStatus.id.desc()).first()
        assert row.borne_id is None
        assert "" not in _borne_status_snapshots()


def test_borne_id_vide_historique_exclu_des_snapshots(application):
    """Des lignes écrites avant la normalisation peuvent encore porter
    ``borne_id = ''`` : le filtre des snapshots les exclut pour ne pas
    dessiner de borne fantôme sur les écrans."""
    from models import _borne_status_snapshots
    with application.app_context():
        db.session.add(PrinterStatus(
            borne_id="", error_code="paper_ok", is_error=False,
            message="statut historique", received_at=datetime.now(time_tz)))
        db.session.commit()
        assert "" not in _borne_status_snapshots()
        # Une vraie borne continue d'apparaître normalement.
        record_printer_status("paper_ok", "Papier OK", borne_id="borne-1")
        assert "borne-1" in _borne_status_snapshots()
