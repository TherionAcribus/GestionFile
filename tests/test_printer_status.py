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


def test_retrait_borne_supprime_tout_son_historique(application):
    """Une borne renommée ou disparue doit pouvoir être retirée : sinon son
    ancienne identité resterait « injoignable » et en alerte pour toujours."""
    from models import _borne_status_snapshots, retire_borne
    with application.app_context():
        record_printer_status("paper_ok", "Papier OK", borne_id="ancienne")
        record_printer_status("heartbeat", "", borne_id="ancienne")
        record_printer_status("paper_ok", "Papier OK", borne_id="autre")
        assert "ancienne" in _borne_status_snapshots()

        deleted = retire_borne("ancienne")

        assert deleted == 2
        assert "ancienne" not in _borne_status_snapshots()
        assert "autre" in _borne_status_snapshots()


# --- Retour à la normale après une panne (imprimante éteinte puis rallumée) --
#
# Bug signalé : imprimante coupée -> bandeau à la première impression ;
# rallumée -> on imprime de nouveau, mais bandeau borne ET alerte admin
# restaient. Cause : le statut de reprise (« init_ok », même message qu'au
# démarrage) était dédoublonné contre la ligne du DÉMARRAGE, plus ancienne
# que l'erreur : cette vieille ligne était rafraîchie en place, et l'erreur
# restait la plus récente — donc l'état courant.

def test_reprise_apres_panne_redevient_l_etat_courant(application):
    with application.app_context():
        record_printer_status("init_ok", "Imprimante USB initialisée avec succès.",
                              borne_id="borne-1")
        record_printer_status("error_print", "Erreur USB lors de l'impression",
                              borne_id="borne-1")
        assert borne_printing_available("borne-1") is False

        # Imprimante rallumée : le gestionnaire de santé la rouvre.
        assert record_printer_status(
            "init_ok", "Imprimante USB initialisée avec succès.",
            borne_id="borne-1") is True

        assert borne_printing_available("borne-1") is True
        assert get_printer_error() is False
        assert borne_overview()[0]["code"] == "init_ok"
        # Le journal se termine par la reprise, pas par l'erreur.
        assert get_printer_infos()[-1]["error_code"] == "init_ok"


def test_pannes_repetees_restent_chacune_visibles(application):
    with application.app_context():
        for _ in range(3):
            record_printer_status("error_print", "Erreur USB", borne_id="borne-1")
            assert borne_printing_available("borne-1") is False
            record_printer_status("init_ok", "Initialisée", borne_id="borne-1")
            assert borne_printing_available("borne-1") is True
        codes = [info["error_code"] for info in get_printer_infos()]
        assert codes == ["error_print", "init_ok"] * 3


def test_statut_repete_sans_changement_reste_une_seule_ligne(application):
    """Le dédoublonnage reste utile quand RIEN n'a changé entre-temps :
    contrôle papier périodique, renvoi après réessai réseau."""
    with application.app_context():
        record_printer_status("no_paper", "Plus de papier", borne_id="borne-1")
        record_printer_status(PRINTER_HEARTBEAT_CODE, "Borne en ligne", borne_id="borne-1")
        assert record_printer_status("no_paper", "Plus de papier", borne_id="borne-1") is False
        assert PrinterStatus.query.filter_by(error_code="no_paper").count() == 1


def test_reprise_n_est_pas_masquee_par_une_autre_borne(application):
    with application.app_context():
        record_printer_status("init_ok", "Initialisée", borne_id="borne-1")
        record_printer_status("init_ok", "Initialisée", borne_id="borne-2")
        record_printer_status("error_print", "Erreur USB", borne_id="borne-1")
        record_printer_status("init_ok", "Initialisée", borne_id="borne-1")
        assert borne_printing_available("borne-1") is True
        assert get_printer_error() is False


# --- Arrêt volontaire annoncé par la borne (menu de maintenance, F10) -------------


def _age_borne(borne_id, seconds):
    """Recule le dernier contact de ``borne_id`` de ``seconds``."""
    when = datetime.now(time_tz) - timedelta(seconds=seconds)
    db.session.query(PrinterStatus).filter_by(
        borne_id=borne_id).update({"received_at": when})
    db.session.commit()


def _silent_after(code, seconds, borne_id="borne-1"):
    record_printer_status(PRINTER_HEARTBEAT_CODE, "Borne en ligne",
                          borne_id=borne_id)
    record_printer_status("paper_ok", "OK", borne_id=borne_id)
    _age_borne(borne_id, seconds + 5)
    # Le dernier message, juste avant le silence.
    record_printer_status(code, "Annonce de la borne", borne_id=borne_id,
                          is_error=False)
    db.session.query(PrinterStatus).filter_by(
        borne_id=borne_id, error_code=code).update({
            "received_at": datetime.now(time_tz) - timedelta(seconds=seconds)})
    db.session.commit()


def test_borne_en_maintenance_n_est_pas_injoignable(application):
    from models import BORNE_MAINTENANCE, KIOSK_MAINTENANCE_CODE, borne_reachability
    with application.app_context():
        _silent_after(KIOSK_MAINTENANCE_CODE, PRINTER_STATUS_STALE_SECONDS + 60)

        overview = {b["borne_id"]: b for b in borne_overview()}
        assert overview["borne-1"]["state"] == BORNE_MAINTENANCE
        assert overview["borne-1"]["reachable"] is False
        # Ni alerte globale, ni ligne « injoignable », ni notification.
        assert get_printer_error() is False
        assert not [i for i in get_printer_infos()
                    if i["error_code"] == KIOSK_OFFLINE_CODE]
        assert borne_reachability() == {"borne-1": True}


def test_maintenance_trop_longue_redevient_injoignable(application):
    """La borne redémarre seule après sa configuration : un silence plus long
    n'est plus expliqué par la maintenance."""
    from models import (
        BORNE_OFFLINE,
        KIOSK_MAINTENANCE_CODE,
        KIOSK_MAINTENANCE_MAX_SECONDS,
        borne_reachability,
    )
    with application.app_context():
        _silent_after(KIOSK_MAINTENANCE_CODE, KIOSK_MAINTENANCE_MAX_SECONDS + 60)
        overview = {b["borne_id"]: b for b in borne_overview()}
        assert overview["borne-1"]["state"] == BORNE_OFFLINE
        assert get_printer_error() is True
        assert borne_reachability() == {"borne-1": False}


def test_application_quittee_reste_arretee_sans_alerte(application):
    from models import BORNE_STOPPED, KIOSK_STOPPED_CODE, borne_reachability
    with application.app_context():
        _silent_after(KIOSK_STOPPED_CODE, 24 * 3600)
        overview = {b["borne_id"]: b for b in borne_overview()}
        assert overview["borne-1"]["state"] == BORNE_STOPPED
        assert get_printer_error() is False
        assert borne_reachability() == {"borne-1": True}


def test_annonce_d_arret_ne_masque_pas_l_etat_imprimante(application):
    """kiosk_maintenance est un évènement : l'état papier reste le dernier
    statut métier."""
    from models import KIOSK_MAINTENANCE_CODE
    with application.app_context():
        record_printer_status("no_paper", "Plus de papier", borne_id="borne-1")
        record_printer_status(KIOSK_MAINTENANCE_CODE, "Configuration",
                              borne_id="borne-1", is_error=False)
        overview = {b["borne_id"]: b for b in borne_overview()}
        assert overview["borne-1"]["code"] == "no_paper"
        assert overview["borne-1"]["state"] == "online"


def test_borne_revenue_apres_maintenance_est_en_ligne(application):
    from models import BORNE_ONLINE, KIOSK_MAINTENANCE_CODE
    with application.app_context():
        _silent_after(KIOSK_MAINTENANCE_CODE, PRINTER_STATUS_STALE_SECONDS + 60)
        record_printer_status(PRINTER_HEARTBEAT_CODE, "Borne en ligne",
                              borne_id="borne-1")
        overview = {b["borne_id"]: b for b in borne_overview()}
        assert overview["borne-1"]["state"] == BORNE_ONLINE


def test_silence_sans_annonce_reste_injoignable(application):
    """Un autre incident borne (kiosk_*) n'explique pas un silence."""
    from models import BORNE_OFFLINE
    with application.app_context():
        _silent_after("kiosk_page_dead", PRINTER_STATUS_STALE_SECONDS + 60)
        overview = {b["borne_id"]: b for b in borne_overview()}
        assert overview["borne-1"]["state"] == BORNE_OFFLINE
        assert get_printer_error() is True


def test_codes_alignes_avec_la_borne():
    """Mêmes codes que maintenance_pin.SERVER_EVENT_* de la borne."""
    from models import KIOSK_EVENT_PREFIX, KIOSK_MAINTENANCE_CODE, KIOSK_STOPPED_CODE
    assert KIOSK_MAINTENANCE_CODE == "kiosk_maintenance"
    assert KIOSK_STOPPED_CODE == "kiosk_stopped"
    for code in (KIOSK_MAINTENANCE_CODE, KIOSK_STOPPED_CODE):
        assert code.startswith(KIOSK_EVENT_PREFIX)
