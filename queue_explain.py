"""Règles lisibles de la page File d'attente (noyau pur).

Statuts d'un patient, dans l'ordre du parcours :
``pending`` (ticket en cours d'impression, pas encore dans la file) →
``standing`` (en attente) → ``calling`` (appelé à un comptoir) →
``ongoing`` (au comptoir) → ``done`` (servi).

Aucune dépendance Flask/SQLAlchemy.
"""

from __future__ import annotations

from datetime import datetime

# Statuts modifiables à la main depuis l'administration (pending est géré par
# le flux d'impression de la borne).
EDITABLE_STATUSES = ("standing", "calling", "ongoing", "done")

# Statuts qui supposent un comptoir.
STATUSES_WITH_COUNTER = ("calling", "ongoing")

STATUS_LABELS = {
    "pending": "Ticket en impression",
    "standing": "En attente",
    "calling": "Appelé",
    "ongoing": "Au comptoir",
    "done": "Servi",
}

STATUS_BADGES = {
    "pending": "text-bg-light border",
    "standing": "text-bg-primary",
    "calling": "text-bg-warning",
    "ongoing": "text-bg-success",
    "done": "text-bg-secondary",
}


def status_label(status) -> str:
    return STATUS_LABELS.get(status, status or "?")


def status_badge(status) -> str:
    return STATUS_BADGES.get(status, "text-bg-light border")


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def minutes_between(start, now) -> int | None:
    """Minutes entières écoulées (``None`` si ``start`` est absent).

    Les horodatages sont enregistrés en heure locale de l'application :
    on compare en heure « naïve » des deux côtés.
    """
    if start is None or now is None:
        return None
    return max(0, int((_naive(now) - _naive(start)).total_seconds() // 60))


def describe_minutes(minutes) -> str:
    """« à l'instant », « 12 min », « 1 h 05 »."""
    if minutes is None:
        return ""
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


def validate_edit(status, counter_id) -> str | None:
    """Message d'erreur si la correction manuelle est incohérente, sinon None."""
    if status not in EDITABLE_STATUSES:
        return "Statut inconnu."
    if status in STATUSES_WITH_COUNTER and not counter_id:
        return f"Un patient « {status_label(status)} » doit être rattaché à un comptoir."
    return None
