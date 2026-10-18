"""Sante du temps reel remontee par les App comptoir (production)."""
import ast
import os
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("SKIP_EVENTLET_PATCH", "1")
os.environ.setdefault("SKIP_STARTUP_HOOKS", "1")

import pytest
from flask import Flask

from counter_realtime_health import (ReportError, normalize_period, parse_report,
                                     period_start, summarize, summary_message)
from models import Counter, CounterRealtimeHealth, db

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 18, 14, 25)


# --- Validation du rapport --------------------------------------------------------

def test_form_strings_are_parsed_and_bounded():
    report = parse_report({'events': '12', 'gaps': '1', 'missed_events': '3',
                           'connected_seconds': '900', 'window_seconds': '300',
                           'app_version': 'x' * 60})
    assert report['events'] == 12
    assert report['connected_seconds'] == 300  # jamais plus que la fenetre
    assert report['app_version'] == 'x' * 40
    assert report['stale_events'] == 0


def test_missed_events_cover_at_least_one_per_gap():
    assert parse_report({'gaps': 4, 'missed_events': 1})['missed_events'] == 4


@pytest.mark.parametrize('payload', [None, [], {'events': -1}, {'gaps': 'abc'},
                                     {'disconnects': True}])
def test_invalid_reports_are_refused(payload):
    with pytest.raises(ReportError):
        parse_report(payload)


def test_huge_values_are_capped():
    assert parse_report({'events': 10**12})['events'] == 1_000_000


# --- Resume ------------------------------------------------------------------------

def _row(counter_id, **values):
    row = {'counter_id': counter_id, 'hour': NOW, 'updated_at': NOW,
           'app_version': None, 'events': 0, 'gaps': 0, 'missed_events': 0,
           'reconnect_gaps': 0, 'stale_events': 0, 'disconnects': 0,
           'connected_seconds': 3600}
    row.update(values)
    return row


def test_summary_flags_counters_by_gaps_per_connected_hour():
    rows = [_row(1, events=500), _row(1, events=300),
            _row(2, events=400, gaps=1, missed_events=1),
            _row(3, events=100, gaps=5, missed_events=9, connected_seconds=1800)]
    summary = summarize(rows, {1: 'C1', 2: 'C2', 3: 'C3'})
    by_name = {item['counter_name']: item for item in summary['counters']}
    assert by_name['C1']['status'] == 'ok'
    assert by_name['C1']['events'] == 800
    assert by_name['C1']['connected_hours'] == 2
    assert by_name['C2']['status'] == 'watch'
    assert by_name['C3']['status'] == 'issue'
    assert by_name['C3']['gaps_per_hour'] == 10
    assert [item['counter_name'] for item in summary['counters']] == ['C3', 'C2', 'C1']
    assert summary['status'] == 'issue'
    assert summary['totals']['missed_events'] == 10


def test_reconnection_gaps_alone_are_not_a_loss():
    summary = summarize([_row(1, events=50, reconnect_gaps=3, disconnects=3)])
    assert summary['status'] == 'ok'
    tone, text = summary_message(summary, 1)
    assert tone == 'success' and '3 coupure(s)' in text


def test_messages_for_empty_and_lossy_periods():
    assert summary_message(summarize([]), 7)[0] == 'secondary'
    tone, text = summary_message(summarize(
        [_row(9, events=10, gaps=3, missed_events=3)], {9: 'Caisse'}), 30)
    assert tone == 'danger'
    assert 'Caisse' in text and 'les 30 derniers jours' in text


def test_periods():
    assert normalize_period('7') == 7
    assert normalize_period('999') == 1
    assert normalize_period(None) == 1
    assert period_start(NOW, 1) == datetime(2026, 10, 17, 15, 0)
    assert period_start(NOW, 7) == datetime(2026, 10, 11, 14, 0)


# --- Route et persistance --------------------------------------------------------------

@pytest.fixture()
def app():
    from routes.counter import counter_bp
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='secret-test-realtime-health',
                      SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    app.register_blueprint(counter_bp)
    with app.app_context():
        db.create_all()
        db.session.add(Counter(id=1, name='Caisse 1', is_active=True))
        db.session.commit()
        yield app


def _token(app):
    from auth_utils import generate_app_token
    with app.app_context():
        return generate_app_token()


def test_report_requires_the_app_token(app):
    client = app.test_client()
    url = '/api/counter/1/realtime_health'
    assert client.post(url, data={'events': 3}).status_code == 401
    assert client.post(url, data={'events': 3},
                       headers={'X-App-Token': 'bidon'}).status_code == 401


def test_reports_of_the_same_hour_are_merged(app):
    client = app.test_client()
    headers = {'X-App-Token': _token(app)}
    url = '/api/counter/1/realtime_health'
    assert client.post(url, data={'events': 3, 'gaps': 1, 'missed_events': 2,
                                  'window_seconds': 300, 'connected_seconds': 300},
                       headers=headers).status_code == 200
    assert client.post(url, json={'events': 4, 'disconnects': 1},
                       headers=headers).status_code == 200
    rows = CounterRealtimeHealth.query.all()
    assert len(rows) == 1
    row = rows[0]
    assert (row.events, row.gaps, row.missed_events, row.disconnects, row.reports) == (7, 1, 2, 1, 2)
    assert row.hour.minute == 0


def test_unknown_counter_and_invalid_payload(app):
    client = app.test_client()
    headers = {'X-App-Token': _token(app)}
    assert client.post('/api/counter/99/realtime_health', data={'events': 1},
                       headers=headers).status_code == 404
    response = client.post('/api/counter/1/realtime_health', data={'events': -5},
                           headers=headers)
    assert response.status_code == 400
    assert response.get_json() == {'error': 'invalid_events'}


def test_service_separates_hours_purges_and_summarizes(app):
    from services import counter_realtime_health_service as service
    report = parse_report({'events': 5, 'connected_seconds': 300, 'window_seconds': 300})
    old = NOW - timedelta(days=40)
    service._last_purge['at'] = -1e9
    assert service.record_report(1, report, now=old)
    service._last_purge['at'] = -1e9
    assert service.record_report(1, report, now=NOW)
    assert service.record_report(1, report, now=NOW + timedelta(hours=1))
    hours = sorted(row.hour for row in CounterRealtimeHealth.query.all())
    # La ligne de 40 jours a ete purgee lors de l'ecriture suivante.
    assert hours == [datetime(2026, 10, 18, 14), datetime(2026, 10, 18, 15)]
    summary = service.load_summary(1, now=NOW + timedelta(hours=1))
    assert summary['counters'][0]['counter_name'] == 'Caisse 1'
    assert summary['counters'][0]['events'] == 10
    assert service.record_report(42, report, now=NOW) is False


# --- Invariant : pas de faux trou de revision ---------------------------------------------

def test_only_the_broadcast_path_bumps_the_queue_revision():
    """Toute increment de revision doit etre diffuse : un increment silencieux
    fait voir aux comptoirs un evenement manque (faux trou, resync inutile).
    Seul ``communication.communikation`` incremente, puis diffuse."""
    offenders = []
    for path in ROOT.rglob('*.py'):
        relative = path.relative_to(ROOT).as_posix()
        if relative.split('/')[0] in {'tests', 'migrations', '.venv'} or relative.startswith('.'):
            continue
        if relative in {'models.py', 'communication.py'}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeDecodeError, PermissionError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and
                    getattr(node.func, 'id', getattr(node.func, 'attr', None)) == 'bump_queue_revision'):
                offenders.append(f'{relative}:{node.lineno}')
    assert offenders == []
