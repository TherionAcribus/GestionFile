"""Règles lisibles de la page Équipe (noyau pur).

- Les initiales sont le CODE DE CONNEXION au comptoir, comparé sans tenir
  compte de la casse (``routes.counter.update_counter_staff``) : l'unicité
  doit l'être aussi, sinon « MA » et « ma » coexistent et la connexion prend
  le premier trouvé.
- Les compétences (activités) décident des patients qu'un membre peut
  appeler (``python.engine.algo_choice_next_patient``).
- Une demande nominative (activité ``is_staff``) n'est appelable que par un
  membre qui l'a dans ses compétences.

Aucune dépendance Flask/SQLAlchemy.
"""

from __future__ import annotations

COMPETENCES_ALL = "all"
COMPETENCES_NONE = "none"
COMPETENCES_SOME = "some"

# Valeurs parasites du champ « langue » (colonne créée avec default=False).
_EMPTY_LANGUAGE = {"", "false", "none", "0"}


def normalize_initials(value) -> str:
    return (value or "").strip().upper()


def initials_taken(initials, others) -> bool:
    """Vrai si ``initials`` est déjà utilisé par l'un des ``others``
    (objets exposant ``initials``), sans tenir compte de la casse."""
    wanted = normalize_initials(initials)
    return any(normalize_initials(o.initials) == wanted for o in others or ())


def clean_language(value) -> str:
    """Texte « langues parlées » affichable (vide pour les valeurs parasites)."""
    if value is None or value is False:
        return ""
    text = str(value).strip()
    return "" if text.lower() in _EMPTY_LANGUAGE else text


def competences(member_activity_ids, activities):
    """``(code, noms)`` : toutes / aucune / certaines des activités ordinaires.

    ``activities`` : activités ORDINAIRES (hors demandes nominatives), qui
    servent de référence pour « toutes ».
    """
    ids = set(member_activity_ids or ())
    names = [a.name for a in activities if a.id in ids]
    if not activities or not names:
        return COMPETENCES_NONE, []
    if len(names) == len(activities):
        return COMPETENCES_ALL, names
    return COMPETENCES_SOME, names


def nominative_for(member_id, nominative_activities):
    """Demandes nominatives qui désignent ce membre."""
    return [a for a in nominative_activities or () if a.staff_id == member_id]


def nominative_missing(member_id, member_activity_ids, nominative_activities):
    """Noms des demandes nominatives pour ce membre qu'il ne peut PAS appeler
    (absentes de ses compétences) : le patient attendrait indéfiniment."""
    ids = set(member_activity_ids or ())
    return [a.name for a in nominative_for(member_id, nominative_activities)
            if a.id not in ids]
