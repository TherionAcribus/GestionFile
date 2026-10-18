"""Persistance des rapports de sante temps reel des App comptoir.

Regles metier et validation : ``counter_realtime_health`` (noyau pur). Ici :
fusion d'un rapport dans la ligne horaire du comptoir, purge de retention et
lecture du resume pour l'administration.
"""

import time
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from config import time_tz
from counter_realtime_health import (COUNTER_FIELDS, hour_bucket, period_start,
                                     retention_cutoff, summarize)
from models import Counter, CounterRealtimeHealth, db

# Purge opportuniste : au plus une fois par heure et par processus, sur le
# chemin d'ecriture (pas de tache planifiee supplementaire).
_PURGE_INTERVAL_SECONDS = 3600
_last_purge = {'at': 0.0}


def _now_local_naive(now=None):
    value = now or datetime.now(time_tz)
    if value.tzinfo is not None:
        value = value.astimezone(time_tz).replace(tzinfo=None)
    return value


def _merge(row, report, now):
    for key in COUNTER_FIELDS:
        setattr(row, key, (getattr(row, key) or 0) + report[key])
    row.reports = (row.reports or 0) + 1
    if report.get('app_version'):
        row.app_version = report['app_version']
    row.updated_at = now


def record_report(counter_id, report, now=None):
    """Ajoute un rapport valide a la ligne horaire du comptoir.

    Retourne False si le comptoir n'existe pas. Deux processus qui creent la
    meme ligne en meme temps : la contrainte d'unicite en refuse un, qui
    retente alors en mise a jour.
    """
    now = _now_local_naive(now)
    if db.session.get(Counter, counter_id) is None:
        return False
    hour = hour_bucket(now)
    for attempt in range(2):
        row = CounterRealtimeHealth.query.filter_by(
            counter_id=counter_id, hour=hour).first()
        if row is None:
            row = CounterRealtimeHealth(counter_id=counter_id, hour=hour,
                                        **dict.fromkeys(COUNTER_FIELDS, 0),
                                        reports=0)
            db.session.add(row)
        _merge(row, report, now)
        try:
            db.session.commit()
            break
        except IntegrityError:
            db.session.rollback()
            if attempt:
                raise
    _purge_if_due(now)
    return True


def _purge_if_due(now):
    clock = time.monotonic()
    if clock - _last_purge['at'] < _PURGE_INTERVAL_SECONDS:
        return
    _last_purge['at'] = clock
    CounterRealtimeHealth.query.filter(
        CounterRealtimeHealth.hour < retention_cutoff(now)).delete(
        synchronize_session=False)
    db.session.commit()


def load_summary(days, now=None):
    now = _now_local_naive(now)
    rows = CounterRealtimeHealth.query.filter(
        CounterRealtimeHealth.hour >= period_start(now, days)).all()
    names = dict(db.session.query(Counter.id, Counter.name).all())
    return summarize(
        [{'counter_id': row.counter_id, 'hour': row.hour,
          'updated_at': row.updated_at, 'app_version': row.app_version,
          **{key: getattr(row, key) for key in COUNTER_FIELDS}} for row in rows],
        names)
