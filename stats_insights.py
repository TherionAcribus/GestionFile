"""Tableau de bord des statistiques : période, indicateurs et répartitions
(noyau pur, sans Flask ni SQLAlchemy).

Entrée : des lignes détaillées ``(timestamp, timestamp_counter, timestamp_end,
status, activity_id, counter_id, language_id, overtaken)`` — patients du jour
(``Patient``) et journées archivées (``PatientHistory``), déjà restreintes à
la période et aux filtres, statuts « jamais entrés dans la file » exclus.

Sorties pensées pour décider (effectifs, horaires, qualité de service) :
indicateurs clés comparés à la période précédente, affluence moyenne par
heure et par jour de semaine, attente selon l'heure d'arrivée, répartition
des attentes et détail par motif / comptoir / langue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

PERIODS = (
    ("today", "Aujourd'hui"),
    ("yesterday", "Hier"),
    ("7", "7 jours"),
    ("28", "28 jours"),
    ("90", "3 mois"),
    ("365", "12 mois"),
    ("custom", "Personnalisée"),
)
DEFAULT_PERIOD = "28"
MAX_DAYS = 366

WEEKDAYS_FR = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")

# Seuil de « longue attente » affiché dans les indicateurs.
LONG_WAIT_MINUTES = 15

# Tranches de la répartition des attentes (bornes hautes exclues, minutes).
WAIT_BUCKETS = ((5, "< 5 min"), (15, "5 à 15 min"), (30, "15 à 30 min"), (None, "30 min et plus"))

# Durées aberrantes ignorées (horloge décalée, patient resté « en cours »
# jusqu'à la purge nocturne…) — même règle que l'historique détaillé.
MAX_PLAUSIBLE_MINUTES = 12 * 60


@dataclass(frozen=True)
class Period:
    key: str
    date_from: date
    date_to: date
    error: str | None = None

    @property
    def days(self) -> int:
        return (self.date_to - self.date_from).days + 1

    @property
    def is_today_only(self) -> bool:
        return self.key == "today"

    def bounds(self):
        """``(début inclus, fin exclue)`` en datetimes naïfs (heure locale)."""
        return (datetime.combine(self.date_from, datetime.min.time()),
                datetime.combine(self.date_to + timedelta(days=1), datetime.min.time()))

    def previous(self) -> "Period":
        """Période de même durée, juste avant (comparaison)."""
        end = self.date_from - timedelta(days=1)
        return Period("previous", end - timedelta(days=self.days - 1), end)

    def label(self) -> str:
        if self.date_from == self.date_to:
            return self.date_from.strftime("%d/%m/%Y")
        return f"du {self.date_from.strftime('%d/%m/%Y')} au {self.date_to.strftime('%d/%m/%Y')}"


def _parse_date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def parse_period(get, today: date) -> Period:
    """Période demandée (``period`` + ``start_date``/``end_date`` si personnalisée)."""
    key = (get("period") or DEFAULT_PERIOD).strip()
    if key not in dict(PERIODS):
        key = DEFAULT_PERIOD
    if key == "today":
        return Period(key, today, today)
    if key == "yesterday":
        day = today - timedelta(days=1)
        return Period(key, day, day)
    if key == "custom":
        start, end = _parse_date(get("start_date")), _parse_date(get("end_date"))
        if start is None or end is None:
            default = int(DEFAULT_PERIOD)
            return Period(key, today - timedelta(days=default - 1), today,
                          error="Dates manquantes ou invalides : 28 derniers jours affichés.")
        if end < start:
            start, end = end, start
        if (end - start).days + 1 > MAX_DAYS:
            start = end - timedelta(days=MAX_DAYS - 1)
            return Period(key, start, end, error=f"Période limitée à {MAX_DAYS} jours.")
        return Period(key, start, end)
    days = int(key)
    return Period(key, today - timedelta(days=days - 1), today)


def minutes(start, end):
    if start is None or end is None:
        return None
    value = (end - start).total_seconds() / 60
    if value < 0 or value > MAX_PLAUSIBLE_MINUTES:
        return None
    return value


def _avg(values):
    return sum(values) / len(values) if values else None


def _percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def _pct(part, whole):
    return round(100 * part / whole) if whole else None


def kpis(rows) -> dict:
    """Indicateurs clés d'un ensemble de lignes."""
    count = served = cancelled = overtaken_patients = 0
    waits, counter_times = [], []
    days = set()
    for ts, ts_counter, ts_end, status, _act, _ctr, _lang, overtaken in rows:
        count += 1
        if ts is not None:
            days.add(ts.date())
        if status == "done":
            served += 1
        elif status == "cancelled":
            cancelled += 1
        if overtaken:
            overtaken_patients += 1
        wait = minutes(ts, ts_counter)
        if wait is not None:
            waits.append(wait)
        at_counter = minutes(ts_counter, ts_end)
        if at_counter is not None:
            counter_times.append(at_counter)
    long_waits = sum(1 for w in waits if w >= LONG_WAIT_MINUTES)
    return {
        "count": count,
        "days": len(days),
        "per_day": count / len(days) if days else None,
        "served": served,
        "served_pct": _pct(served, count),
        "cancelled": cancelled,
        "cancelled_pct": _pct(cancelled, count),
        "avg_wait": _avg(waits),
        "median_wait": _percentile(waits, 50),
        "p90_wait": _percentile(waits, 90),
        "long_wait_pct": _pct(long_waits, len(waits)),
        "avg_counter": _avg(counter_times),
        "overtaken_patients": overtaken_patients,
    }


def delta(current, previous):
    """Variation en % (arrondie), ``None`` si non comparable."""
    if current is None or not previous:
        return None
    return round(100 * (current - previous) / previous)


def by_hour(rows):
    """Affluence moyenne (patients par jour d'activité) et attente moyenne,
    selon l'heure d'arrivée. Limité à la plage horaire réellement ouverte."""
    counts = [0] * 24
    waits = [[] for _ in range(24)]
    days = set()
    for ts, ts_counter, *_rest in rows:
        if ts is None:
            continue
        days.add(ts.date())
        counts[ts.hour] += 1
        wait = minutes(ts, ts_counter)
        if wait is not None:
            waits[ts.hour].append(wait)
    hours = [h for h in range(24) if counts[h]]
    if not hours:
        return []
    nb_days = len(days) or 1
    return [{"hour": h, "label": f"{h:02d}h",
             "avg_patients": counts[h] / nb_days,
             "avg_wait": _avg(waits[h])}
            for h in range(min(hours), max(hours) + 1)]


def by_weekday(rows):
    """Affluence moyenne par jour de semaine (sur les jours ayant eu des patients)."""
    counts = [0] * 7
    days_seen = [set() for _ in range(7)]
    for ts, *_rest in rows:
        if ts is None:
            continue
        counts[ts.weekday()] += 1
        days_seen[ts.weekday()].add(ts.date())
    return [{"weekday": i, "label": WEEKDAYS_FR[i],
             "avg_patients": counts[i] / len(days_seen[i]) if days_seen[i] else 0,
             "days": len(days_seen[i])}
            for i in range(7) if days_seen[i]]


def wait_distribution(rows):
    """Nombre de patients par tranche d'attente (voir WAIT_BUCKETS)."""
    result = [{"label": label, "count": 0} for _limit, label in WAIT_BUCKETS]
    for ts, ts_counter, *_rest in rows:
        wait = minutes(ts, ts_counter)
        if wait is None:
            continue
        for index, (limit, _label) in enumerate(WAIT_BUCKETS):
            if limit is None or wait < limit:
                result[index]["count"] += 1
                break
    return result


_DIMENSION_INDEX = {"activity": 4, "counter": 5, "language": 6}


def breakdown(rows, dimension, names):
    """Détail par motif / comptoir / langue : effectif, part, attente et temps
    au comptoir moyens. Trié par effectif décroissant."""
    index = _DIMENSION_INDEX[dimension]
    groups = {}
    total = 0
    for row in rows:
        key = row[index]
        if key is None:
            continue
        total += 1
        group = groups.setdefault(key, {"count": 0, "waits": [], "counter": []})
        group["count"] += 1
        wait = minutes(row[0], row[1])
        if wait is not None:
            group["waits"].append(wait)
        at_counter = minutes(row[1], row[2])
        if at_counter is not None:
            group["counter"].append(at_counter)
    items = [{"id": key, "name": names.get(key, f"#{key}"), "count": g["count"],
              "pct": _pct(g["count"], total), "avg_wait": _avg(g["waits"]),
              "avg_counter": _avg(g["counter"])}
             for key, g in groups.items()]
    return sorted(items, key=lambda item: (-item["count"], str(item["name"])))


def peak(hours):
    """Heure la plus chargée (en patients moyens), ou ``None``."""
    if not hours:
        return None
    best = max(hours, key=lambda h: h["avg_patients"])
    return best if best["avg_patients"] > 0 else None


def format_minutes(value) -> str:
    if value is None:
        return "—"
    if value < 1:
        return "< 1 min"
    total = int(round(value))
    return f"{total} min" if total < 60 else f"{total // 60} h {total % 60:02d}"
