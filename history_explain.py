"""Historique détaillé : filtres, durées et résumé (noyau pur).

Une ligne d'historique (``PatientHistory``) porte trois horodatages :
``timestamp`` (arrivée), ``timestamp_counter`` (pris en charge au comptoir)
et ``timestamp_end`` (fin). On en déduit :

- l'**attente** = arrivée → comptoir ;
- le **temps au comptoir** = comptoir → fin.

Aucune dépendance Flask/SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# Période par défaut : les 30 derniers jours (la table croît sans fin ; un
# affichage « tout » serait lent et illisible).
DEFAULT_DAYS = 30

# Préréglages proposés dans la barre de filtres.
PRESETS = (
    ("yesterday", "Hier"),
    ("7", "7 derniers jours"),
    ("30", "30 derniers jours"),
    ("90", "3 derniers mois"),
)

# Durées aberrantes ignorées dans les moyennes (horloge décalée, patient
# resté « en cours » jusqu'à la purge nocturne…).
MAX_PLAUSIBLE_MINUTES = 12 * 60


@dataclass
class HistoryFilters:
    date_from: date
    date_to: date
    activity_id: int | None = None
    counter_id: int | None = None
    statuses: list = field(default_factory=list)
    error: str | None = None

    def as_query_args(self) -> dict:
        """Paramètres d'URL équivalents (lien d'export CSV)."""
        args = {"date_from": self.date_from.isoformat(),
                "date_to": self.date_to.isoformat()}
        if self.activity_id:
            args["activity_id"] = str(self.activity_id)
        if self.counter_id:
            args["counter_id"] = str(self.counter_id)
        if self.statuses:
            args["status"] = ",".join(self.statuses)
        return args


def _parse_date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _parse_id(value):
    text = (value or "").strip()
    return int(text) if text.isdigit() else None


def parse_filters(get, today: date, known_statuses=(), getlist=None) -> HistoryFilters:
    """Filtres depuis la requête (``get`` = ``request.values.get``).

    Préréglage ``preset`` prioritaire ; sinon ``date_from``/``date_to``
    (défaut : 30 derniers jours). Dates inversées : remises dans l'ordre.
    Statuts limités à ``known_statuses`` (liste blanche).
    """
    error = None
    preset = (get("preset") or "").strip()
    if preset == "yesterday":
        date_from = date_to = today - timedelta(days=1)
    elif preset.isdigit():
        date_to = today
        date_from = today - timedelta(days=int(preset) - 1)
    else:
        date_to = _parse_date(get("date_to")) or today
        date_from = _parse_date(get("date_from")) or (date_to - timedelta(days=DEFAULT_DAYS - 1))
        if (get("date_from") and not _parse_date(get("date_from"))) or \
                (get("date_to") and not _parse_date(get("date_to"))):
            error = "Date invalide : période par défaut appliquée."
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    # Cases à cocher (plusieurs « status ») ou liste « a,b » (lien d'export).
    raw = list(getlist("status")) if getlist else [get("status") or ""]
    statuses = []
    for chunk in raw:
        for part in (chunk or "").split(","):
            part = part.strip()
            if part and part in known_statuses and part not in statuses:
                statuses.append(part)
    return HistoryFilters(date_from=date_from, date_to=date_to,
                          activity_id=_parse_id(get("activity_id")),
                          counter_id=_parse_id(get("counter_id")),
                          statuses=statuses, error=error)


def minutes(start, end):
    """Minutes entre deux horodatages, ``None`` si incomplet ou aberrant."""
    if start is None or end is None:
        return None
    value = (end - start).total_seconds() / 60
    if value < 0 or value > MAX_PLAUSIBLE_MINUTES:
        return None
    return value


def format_minutes(value) -> str:
    """« 4 min », « 1 h 05 », « < 1 min », « — »."""
    if value is None:
        return "—"
    if value < 1:
        return "< 1 min"
    total = int(round(value))
    if total < 60:
        return f"{total} min"
    return f"{total // 60} h {total % 60:02d}"


def summarize(rows, served_status="done") -> dict:
    """Résumé d'un ensemble de lignes ``(timestamp, timestamp_counter,
    timestamp_end, status, overtaken)``.

    Moyennes calculées sur les seules durées exploitables.
    """
    count = served = 0
    waits, counters = [], []
    max_overtaken = 0
    for ts, ts_counter, ts_end, status, overtaken in rows:
        count += 1
        if status == served_status:
            served += 1
        wait = minutes(ts, ts_counter)
        if wait is not None:
            waits.append(wait)
        at_counter = minutes(ts_counter, ts_end)
        if at_counter is not None:
            counters.append(at_counter)
        max_overtaken = max(max_overtaken, overtaken or 0)

    def avg(values):
        return sum(values) / len(values) if values else None

    return {
        "count": count,
        "served": served,
        "served_pct": round(100 * served / count) if count else None,
        "avg_wait": avg(waits),
        "avg_counter": avg(counters),
        "max_wait": max(waits) if waits else None,
        "max_overtaken": max_overtaken,
    }


def day_bounds(filters: HistoryFilters):
    """``(début inclus, fin exclue)`` en datetimes naïfs pour la requête."""
    start = datetime.combine(filters.date_from, datetime.min.time())
    end = datetime.combine(filters.date_to + timedelta(days=1), datetime.min.time())
    return start, end
