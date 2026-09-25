"""Explications lisibles des règles de l'algorithme de priorité (noyau pur).

Sert la page /admin/algo : une phrase par règle et, pour l'instant présent,
« en vigueur » ou la raison pour laquelle elle ne l'est pas. Les critères
reprennent EXACTEMENT ceux de ``python.engine.get_applicable_algo_rules``
(bornes inclusives, jour de la semaine) — la page ne doit pas afficher
« en vigueur » une règle que le moteur ignore.

Aucune dépendance Flask/SQLAlchemy : ``rule`` est tout objet exposant les
attributs d'``AlgoRule`` (et ``activity.name``).
"""

from __future__ import annotations

DAY_ORDER = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_LONG_FR = {
    "Mon": "lundi", "Tue": "mardi", "Wed": "mercredi", "Thu": "jeudi",
    "Fri": "vendredi", "Sat": "samedi", "Sun": "dimanche",
}
DAY_SHORT_FR = {
    "Mon": "lun.", "Tue": "mar.", "Wed": "mer.", "Thu": "jeu.",
    "Fri": "ven.", "Sat": "sam.", "Sun": "dim.",
}

# Valeurs par défaut du formulaire qui signifient « pas de limite ».
UNLIMITED = 999

STATE_ACTIVE = "active"
STATE_OFF_DAY = "off_day"
STATE_OFF_HOURS = "off_hours"
STATE_QUEUE_RANGE = "queue_range"


def parse_days(days_csv: str | None) -> list[str]:
    """Jours d'une règle, dans l'ordre lundi → dimanche, sans doublon."""
    wanted = {d.strip() for d in (days_csv or "").split(",") if d.strip()}
    return [d for d in DAY_ORDER if d in wanted]


def describe_days(days_csv: str | None) -> str:
    """« tous les jours », « du lundi au vendredi », « le samedi », « lun., mer. »."""
    days = parse_days(days_csv)
    if not days:
        return "aucun jour"
    if len(days) == 7:
        return "tous les jours"
    if len(days) == 1:
        return f"le {DAY_LONG_FR[days[0]]}"
    indexes = [DAY_ORDER.index(d) for d in days]
    if len(days) >= 3 and indexes == list(range(indexes[0], indexes[-1] + 1)):
        return f"du {DAY_LONG_FR[days[0]]} au {DAY_LONG_FR[days[-1]]}"
    return ", ".join(DAY_SHORT_FR[d] for d in days)


def _hhmm(value) -> str:
    return value.strftime("%H:%M") if value is not None else "--:--"


def describe_hours(rule) -> str:
    start, end = _hhmm(rule.start_time), _hhmm(rule.end_time)
    if start == "00:00" and end in ("23:59", "23:59:59"):
        return "toute la journée"
    return f"de {start} à {end}"


def describe_queue_range(rule) -> str | None:
    """Condition sur la taille de la file, ou ``None`` si elle est toujours vraie."""
    low = rule.min_patients or 0
    high = rule.max_patients
    bounded_high = high is not None and high < UNLIMITED
    if low > 0 and bounded_high:
        return f"quand {low} à {high} patients attendent"
    if low > 0:
        return f"à partir de {low} patient{'s' if low > 1 else ''} en attente"
    if bounded_high:
        return f"tant qu'il y a au plus {high} patient{'s' if high > 1 else ''} en attente"
    return None


def describe_overtaking(max_overtaken) -> str:
    if max_overtaken is None or max_overtaken >= UNLIMITED:
        return "passent devant tous les patients en attente"
    if max_overtaken == 0:
        return "ne passent devant personne (règle sans effet)"
    return (f"passent devant {max_overtaken} patient"
            f"{'s' if max_overtaken > 1 else ''} au maximum")


def summarize_rule(rule) -> str:
    """Phrase complète : qui passe devant, de combien, quand."""
    activity = getattr(getattr(rule, "activity", None), "name", None) or "?"
    parts = [f"Les patients « {activity} » {describe_overtaking(rule.max_overtaken)}",
             describe_days(rule.days_of_week),
             describe_hours(rule)]
    queue = describe_queue_range(rule)
    if queue:
        parts.append(queue)
    return ", ".join(parts) + "."


def rule_state(rule, *, now_time, day_abbr: str, waiting: int) -> tuple[str, str]:
    """État de la règle à l'instant donné : ``(code, explication)``.

    Mêmes critères que le moteur : jour, créneau [début, fin] inclusif,
    effectif de la file [min, max] inclusif.
    """
    if day_abbr not in parse_days(rule.days_of_week):
        return STATE_OFF_DAY, "Pas aujourd'hui."
    if not (rule.start_time <= now_time <= rule.end_time):
        return STATE_OFF_HOURS, f"Hors créneau ({describe_hours(rule)})."
    if not (rule.min_patients <= waiting <= rule.max_patients):
        return (STATE_QUEUE_RANGE,
                f"{waiting} patient{'s' if waiting > 1 else ''} en attente : "
                f"hors de la plage {rule.min_patients}–{rule.max_patients}.")
    return STATE_ACTIVE, "En vigueur maintenant."
