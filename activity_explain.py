"""Explications lisibles des activités et plages horaires (noyau pur).

Sert la page /admin/activity : résumé d'une plage horaire, activité « dans
ses horaires » à un instant donné, lettres partagées entre activités.

Règle : une activité est proposée EN CONTINU par défaut. Les plages horaires
sont une restriction facultative — sans aucune plage, l'activité est
toujours proposée (auparavant : jamais).

``is_open_at`` est aussi la règle utilisée par
``routes.admin_activity.update_bouton_after_scheduler_changed`` pour
(dés)activer les boutons de la borne : la page affiche donc exactement ce
que la borne applique.

Aucune dépendance Flask/SQLAlchemy : les objets exposent les attributs des
modèles (``start_time``, ``end_time``, ``weekdays[].english_name``…).
"""

from __future__ import annotations

from algo_explain import describe_days

# english_name des Weekday (« monday ») → abréviations d'algo_explain (« Mon »).
_ENGLISH_TO_ABBR = {
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed", "thursday": "Thu",
    "friday": "Fri", "saturday": "Sat", "sunday": "Sun",
}


def weekday_codes(weekdays) -> str:
    """CSV d'abréviations (« Mon,Tue ») pour les jours d'une plage."""
    codes = []
    for day in weekdays or ():
        code = _ENGLISH_TO_ABBR.get((getattr(day, "english_name", "") or "").strip().lower())
        if code:
            codes.append(code)
    return ",".join(codes)


def _hhmm(value) -> str:
    return value.strftime("%H:%M") if value is not None else "--:--"


def describe_schedule(schedule) -> str:
    """« du lundi au vendredi, 09:00–19:00 » / « tous les jours, toute la journée »."""
    days = describe_days(weekday_codes(schedule.weekdays))
    start, end = _hhmm(schedule.start_time), _hhmm(schedule.end_time)
    if start == "00:00" and end in ("23:59", "23:59:59"):
        hours = "toute la journée"
    else:
        hours = f"{start}–{end}"
    return f"{days}, {hours}"


_ALL_DAYS = set(_ENGLISH_TO_ABBR)


def _is_full_time(schedule) -> bool:
    """Plage 00:00–23:59, les 7 jours (ex. plage par défaut « continu »)."""
    if schedule.start_time is None or schedule.end_time is None:
        return False
    days = {(getattr(w, "english_name", "") or "").strip().lower()
            for w in schedule.weekdays or ()}
    return (_hhmm(schedule.start_time) == "00:00"
            and _hhmm(schedule.end_time) in ("23:59", "23:59:59")
            and days >= _ALL_DAYS)


def is_continuous(schedules) -> bool:
    """Vrai si l'activité est proposée en continu : aucune plage, ou une plage
    couvrant toute la semaine, toute la journée."""
    schedules = list(schedules or ())
    return not schedules or any(_is_full_time(s) for s in schedules)


def is_open_at(schedules, weekday_english: str, current_time) -> bool:
    """Vrai si l'activité est proposée à ce jour et cette heure.

    Sans aucune plage : proposée en continu. Sinon, l'une des plages doit
    couvrir ce jour et cette heure (bornes incluses).
    """
    # En continu (y compris via une plage 00:00–23:59 sur 7 jours, qui sinon
    # « fermerait » pendant la dernière minute de la journée).
    if is_continuous(schedules):
        return True
    day = (weekday_english or "").strip().lower()
    for schedule in schedules or ():
        if schedule.start_time is None or schedule.end_time is None:
            continue
        days = {(getattr(w, "english_name", "") or "").strip().lower()
                for w in schedule.weekdays or ()}
        if day in days and schedule.start_time <= current_time <= schedule.end_time:
            return True
    return False


def shared_letters(activities) -> dict:
    """``{id: [noms des autres activités ayant la même lettre]}``.

    En numérotation par activité, deux activités de même lettre partagent une
    seule série (A-1 pour l'une, A-2 pour l'autre) : on ne peut plus les
    distinguer au numéro. La page le signale.
    """
    by_letter: dict[str, list] = {}
    for activity in activities or ():
        letter = (activity.letter or "").strip().upper()
        if letter:
            by_letter.setdefault(letter, []).append(activity)
    result = {}
    for group in by_letter.values():
        if len(group) < 2:
            continue
        for activity in group:
            result[activity.id] = [a.name for a in group if a.id != activity.id]
    return result
