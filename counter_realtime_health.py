"""Sante du temps reel vue par les comptoirs en service (noyau pur).

Le test de charge mesure la livraison des evenements dans un environnement
synthetique (``stress_realtime``). Ce module mesure la meme chose *en
production*, la ou le Wi-Fi d'une officine et les mises en veille ne se
simulent pas : chaque App comptoir compte ce qu'elle observe et l'envoie
toutes les quelques minutes (``POST /api/counter/<id>/realtime_health``).

L'App sait detecter un evenement manque grace a la revision de file : chaque
diffusion porte un numero, un saut de plus d'un cran est un trou. Elle
distingue :

* ``gaps`` / ``missed_events`` : trous alors que la connexion etait etablie
  et l'etat a jour -> evenements reellement perdus en route ;
* ``reconnect_gaps`` : trous constates juste apres une reconnexion, avant le
  rattrapage -> attendus, la coupure les explique ;
* ``stale_events`` : evenements recus en double ou dans le desordre ;
* ``disconnects`` et ``connected_seconds`` : coupures et temps connecte, qui
  servent de denominateur (trous par heure de connexion).

Les rapports sont agreges par comptoir et par heure (une ligne au plus par
comptoir et par heure) puis resumes ici pour l'administration.
"""
from __future__ import annotations

from datetime import datetime, timedelta

COUNTER_FIELDS = ('events', 'gaps', 'missed_events', 'reconnect_gaps',
                  'stale_events', 'disconnects', 'connected_seconds')
MAX_COUNT = 1_000_000
# Un rapport couvre normalement 5 minutes ; apres des envois en echec, l'App
# fusionne les fenetres. Au-dela d'une journee, le rapport est tronque.
MAX_WINDOW_SECONDS = 86_400
RETENTION_DAYS = 30
PERIODS_DAYS = (1, 7, 30)
# Trous « en ligne » par heure de connexion au-dela desquels un comptoir est
# signale. Un trou isole par jour reste normal (redemarrage serveur...).
WATCH_GAPS_PER_HOUR = 0
ISSUE_GAPS_PER_HOUR = 1


class ReportError(ValueError):
    """Rapport refuse : ``code`` court destine a la reponse JSON."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _count(data, key, upper=MAX_COUNT):
    value = data.get(key, 0)
    if isinstance(value, bool):
        raise ReportError(f'invalid_{key}')
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ReportError(f'invalid_{key}') from None
    if value < 0:
        raise ReportError(f'invalid_{key}')
    return min(value, upper)


def parse_report(data):
    """Valide et borne un rapport d'App comptoir. Leve ``ReportError``."""
    if not isinstance(data, dict):
        raise ReportError('invalid_payload')
    report = {key: _count(data, key) for key in COUNTER_FIELDS}
    window = _count(data, 'window_seconds', MAX_WINDOW_SECONDS)
    report['connected_seconds'] = min(report['connected_seconds'],
                                      window or MAX_WINDOW_SECONDS)
    report['missed_events'] = max(report['missed_events'], report['gaps'])
    version = data.get('app_version')
    report['app_version'] = (str(version)[:40] if version not in (None, '')
                             else None)
    return report


def hour_bucket(moment):
    return moment.replace(minute=0, second=0, microsecond=0)


def retention_cutoff(now, days=RETENTION_DAYS):
    return hour_bucket(now) - timedelta(days=days)


def period_start(now, days):
    """Premiere heure incluse : « 24 heures » glissantes pour 1 jour."""
    if days == 1:
        return hour_bucket(now) - timedelta(hours=23)
    return hour_bucket(now) - timedelta(days=days)


def normalize_period(value):
    try:
        days = int(value)
    except (TypeError, ValueError):
        return PERIODS_DAYS[0]
    return days if days in PERIODS_DAYS else PERIODS_DAYS[0]


def _counter_status(gaps_per_hour, gaps):
    if not gaps:
        return 'ok'
    if gaps_per_hour is not None and gaps_per_hour > ISSUE_GAPS_PER_HOUR:
        return 'issue'
    return 'watch'


def summarize(rows, counter_names=None):
    """Resume par comptoir des lignes horaires ``rows`` (dicts).

    Chaque ligne porte ``counter_id``, ``hour``, ``updated_at`` et les champs
    de ``COUNTER_FIELDS``. ``counter_names`` : ``{counter_id: nom}``.
    """
    counter_names = counter_names or {}
    per_counter = {}
    for row in rows:
        entry = per_counter.setdefault(row['counter_id'], {
            'counter_id': row['counter_id'],
            'counter_name': counter_names.get(row['counter_id']),
            **dict.fromkeys(COUNTER_FIELDS, 0),
            'last_report_at': None, 'app_version': None})
        for key in COUNTER_FIELDS:
            entry[key] += int(row.get(key) or 0)
        updated = row.get('updated_at')
        if updated and (entry['last_report_at'] is None or updated > entry['last_report_at']):
            entry['last_report_at'] = updated
            entry['app_version'] = row.get('app_version')

    counters = []
    for entry in per_counter.values():
        hours = entry['connected_seconds'] / 3600
        entry['connected_hours'] = round(hours, 2)
        entry['gaps_per_hour'] = round(entry['gaps'] / hours, 2) if hours >= 0.01 else None
        received = entry['events'] + entry['missed_events']
        entry['loss_percent'] = (entry['missed_events'] / received * 100
                                 if received else 0)
        entry['status'] = _counter_status(entry['gaps_per_hour'], entry['gaps'])
        if isinstance(entry['last_report_at'], datetime):
            entry['last_report_at'] = entry['last_report_at'].isoformat()
        counters.append(entry)
    order = {'issue': 0, 'watch': 1, 'ok': 2}
    counters.sort(key=lambda item: (order[item['status']],
                                    str(item['counter_name'] or item['counter_id'])))

    totals = {key: sum(item[key] for item in counters) for key in COUNTER_FIELDS}
    received = totals['events'] + totals['missed_events']
    totals['loss_percent'] = totals['missed_events'] / received * 100 if received else 0
    totals['connected_hours'] = round(totals['connected_seconds'] / 3600, 2)
    if not counters:
        status = 'empty'
    elif any(item['status'] == 'issue' for item in counters):
        status = 'issue'
    elif any(item['status'] == 'watch' for item in counters):
        status = 'watch'
    else:
        status = 'ok'
    return {'status': status, 'counters': counters, 'totals': totals}


def _fr(value, digits=1):
    return f'{value:.{digits}f}'.replace('.', ',')


def summary_message(summary, days):
    """Phrase affichable et ton Bootstrap pour l'ensemble des comptoirs."""
    period = 'les dernières 24 heures' if days == 1 else f'les {days} derniers jours'
    status = summary.get('status')
    totals = summary.get('totals') or {}
    if status == 'empty':
        return 'secondary', (
            f"Aucun rapport de comptoir sur {period}. Les App comptoir envoient "
            'leurs mesures toutes les 5 minutes une fois connectées (version à jour requise).')
    counters = summary.get('counters') or []
    flagged = [item for item in counters if item['status'] != 'ok']
    if not flagged:
        return 'success', (
            f"Aucun événement temps réel perdu sur {period} : "
            f"{totals.get('events', 0)} reçus par {len(counters)} comptoir(s), "
            f"{totals.get('disconnects', 0)} coupure(s) rattrapée(s).")
    names = ', '.join(str(item['counter_name'] or f"n°{item['counter_id']}")
                      for item in flagged[:3])
    tone = 'danger' if status == 'issue' else 'warning'
    return tone, (
        f"{totals.get('missed_events', 0)} événement(s) perdu(s) en cours de connexion "
        f"sur {period} ({_fr(totals.get('loss_percent') or 0, 2)} %), "
        f"à surveiller sur : {names}. Les comptoirs se resynchronisent "
        "automatiquement ; des pertes répétées indiquent un réseau instable ou "
        'un serveur surchargé.')
