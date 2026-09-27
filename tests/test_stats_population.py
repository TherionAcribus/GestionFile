"""Population des statistiques : seuls les parcours ayant rejoint la file
comptent (audit « parcours patient », point statistiques/archivage).

Avant : ni ``fetch_detailed_data`` ni ``create_daily_stats`` ne filtraient le
statut — une inscription ``pending``/``print_failed``/``expired`` comptait
comme une visite, et la moyenne « temps total » incluait les retraits
(``cancelled`` pose ``timestamp_end``, mais sa fin borne le retrait, pas une
visite).

Désormais :

* les graphiques de comptage et les moyennes ne considèrent que les statuts
  réellement passés par la file (``STATS_EXCLUDED_STATUSES`` écarte les
  inscriptions jamais activées) — ``cancelled`` compte : le patient a occupé
  la file, parfois le comptoir ;
* la métrique ``total`` (durée du parcours) est réservée aux ``done`` :
  c'est le seul statut dont ``timestamp_end`` marque la fin d'une visite.

``create_daily_stats`` et les métriques de durée reposent sur
``timestampdiff`` (MySQL) : sous SQLite elles ne sont vérifiables que
statiquement — comme le fait déjà ``test_history_purge.py``.
"""

import os
import re
from datetime import datetime

import pytest
from flask import Flask

from models import Activity, Patient, PatientHistory, db
from routes.admin_stats import fetch_detailed_data, filter_complete_timestamps
from stats_params import parse_chart_request


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _func_body(source, func):
    m = re.search(r"def " + func + r"\(.*?\n(.*?)(?=\ndef |\Z)", source, re.DOTALL)
    assert m, f"fonction {func} introuvable"
    return m.group(1)


class Args:
    def __init__(self, single=None, multi=None):
        self._single = single or {}
        self._multi = multi or {}

    def get(self, key, default=None):
        return self._single.get(key, default)

    def getlist(self, key):
        return list(self._multi.get(key, []))


# Statuts ayant rejoint la file : ils doivent compter dans les visites.
QUEUED_STATUSES = ("standing", "calling", "ongoing", "done", "cancelled")
# Jamais entrés en file : exclus des statistiques.
EXCLUDED_STATUSES = ("pending", "print_failed", "expired")


@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__, instance_path=str(tmp_path / "instance"))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_BINDS={"users": "sqlite:///:memory:"},
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        db.session.add(Activity(name="Accueil", letter="A"))
        db.session.commit()
    return app


def _activity_id():
    return Activity.query.filter_by(letter="A").one().id


def _seed_patient_history():
    """Une ligne par statut, dans l'historique, sur la même activité."""
    ts = datetime(2026, 1, 5, 10, 0)
    for i, status in enumerate(QUEUED_STATUSES + EXCLUDED_STATUSES):
        db.session.add(PatientHistory(
            call_number=f"H{i}", timestamp=ts, day_of_week="Monday",
            status=status, activity_id=_activity_id(),
        ))
    db.session.commit()


def _seed_patients(now):
    """Une ligne par statut, dans la file du jour, sur la même activité."""
    for i, status in enumerate(QUEUED_STATUSES + EXCLUDED_STATUSES):
        db.session.add(Patient(
            call_number=f"P{i}", timestamp=now,
            status=status, activity_id=_activity_id(),
        ))
    db.session.commit()


def _history_req(chart_type="activities"):
    req = parse_chart_request(Args({
        "chart_type": chart_type,
        "date_type": "history",
        "period_type": "custom",
        "start_date": "2026-01-01",
        "end_date": "2026-01-10",
    }), now=datetime(2026, 6, 1))
    assert req.ok, req.error
    return req


def _current_req(now, chart_type="activities"):
    req = parse_chart_request(Args({
        "chart_type": chart_type,
        "date_type": "current",
    }), now=now)
    assert req.ok, req.error
    return req


# ---------------------------------------------------------------------------
# Comptages détaillés (graphiques de distribution)
# ---------------------------------------------------------------------------

def test_history_exclut_les_parcours_jamais_en_file(app):
    """Régression : pending/print_failed/expired comptaient comme des visites."""
    with app.app_context():
        _seed_patient_history()
        rows = fetch_detailed_data(PatientHistory, _history_req())
        row = next(r for r in rows if r.category == "Accueil")
        assert row.value == len(QUEUED_STATUSES)


def test_history_cancelled_compte_comme_visite(app):
    """« Retiré » a réellement occupé la file : il reste compté."""
    with app.app_context():
        db.session.add(PatientHistory(
            call_number="C1", timestamp=datetime(2026, 1, 5, 10, 0),
            day_of_week="Monday", status="cancelled",
            activity_id=_activity_id(),
        ))
        db.session.commit()
        rows = fetch_detailed_data(PatientHistory, _history_req())
        assert any(r.category == "Accueil" and r.value == 1 for r in rows)


def test_jour_courant_exclut_les_parcours_jamais_en_file(app):
    """Même règle sur ``Patient`` : un pending/print_failed du jour n'est pas
    une visite tant qu'il n'a pas rejoint la file."""
    now = datetime(2026, 6, 1, 15, 0)
    with app.app_context():
        _seed_patients(now)
        rows = fetch_detailed_data(Patient, _current_req(now), join_models=True)
        row = next(r for r in rows if r.category == "Accueil")
        assert row.value == len(QUEUED_STATUSES)


# ---------------------------------------------------------------------------
# Métrique « total » : seuls les parcours menés à terme
# ---------------------------------------------------------------------------

def _compiled_where(query):
    return str(query.statement.compile(compile_kwargs={"literal_binds": True}))


def test_metrique_total_reservee_aux_done(app):
    """Un ``cancelled`` pose timestamp_end mais sa fin borne le retrait :
    il ne doit pas entrer dans la moyenne « temps total »."""
    with app.app_context():
        for model in (Patient, PatientHistory):
            query = filter_complete_timestamps(
                db.session.query(model), model, "total")
            sql = _compiled_where(query)
            assert "status = 'done'" in sql


def test_metriques_attente_et_comptoir_gardent_les_occupants(app):
    """waiting/counter : les horodatages suffisent — un retiré au comptoir a
    réellement attendu et occupé le comptoir (pas de restriction 'done')."""
    with app.app_context():
        for metric in ("waiting", "counter"):
            query = filter_complete_timestamps(
                db.session.query(PatientHistory), PatientHistory, metric)
            where = _compiled_where(query).split("WHERE", 1)[1]
            assert "status" not in where


# ---------------------------------------------------------------------------
# Agrégation (MySQL-only) : vérifications statiques
# ---------------------------------------------------------------------------

def test_create_daily_stats_filtre_la_population():
    """L'agrégat compressé doit suivre la même règle « file réelle » que la
    vue détaillée, sinon les deux moitiés d'un graphique divergeraient."""
    body = _func_body(_read("scheduler_functions.py"), "create_daily_stats")
    assert "STATS_EXCLUDED_STATUSES" in body


def test_create_daily_stats_total_reserve_aux_done():
    """Le CASE doit laisser NULL les lignes non 'done' pour avg_total et
    count_total — sinon un retrait gonfle la moyenne des durées de visite."""
    body = _func_body(_read("scheduler_functions.py"), "create_daily_stats")
    assert "status == 'done'" in body
