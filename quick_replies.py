"""Phrases rapides du menu « ⚡ » de la messagerie de l'App Comptoir.

Module pur (sans Flask ni base) : règles communes aux deux listes.

- **Phrases d'équipe** : réglées dans Admin › Application (clé
  ``app_quick_replies``, une phrase par ligne). Clé absente = phrases
  d'origine ; champ vidé = aucune phrase d'équipe (choix assumé de l'admin).
- **Phrases personnelles** : propres à chaque équipier, rangées sur sa fiche
  (``Pharmacist.quick_replies``) et modifiées depuis l'App.

Les deux listes arrivent dans l'App avec l'état de la messagerie. Les phrases
d'origine doivent rester identiques à ``MessagingController.QUICK_REPLIES``
côté App (repli face à un serveur ancien ; un test le vérifie).
"""

from __future__ import annotations

import re

#: Clé de configuration de la liste d'équipe.
TEAM_KEY = "app_quick_replies"
TEAM_CONFIG_NAME = TEAM_KEY.upper()

#: Phrases d'origine (installation neuve, ou clé jamais enregistrée).
DEFAULT_TEAM = ("J'arrive", "Occupé(e) 2 min", "En réserve", "Un client m'attend")

#: Une phrase rapide se lit d'un coup d'œil dans un menu.
MAX_LENGTH = 120
#: Au-delà, le menu devient une liste à parcourir : on n'y gagne plus de temps.
MAX_TEAM = 12
MAX_PERSONAL = 12

_SPACES = re.compile(r"\s+")


def normalize_phrase(value) -> str:
    """Espaces et retours à la ligne ramenés à une seule espace."""
    if not isinstance(value, str):
        return ""
    return _SPACES.sub(" ", value).strip()


def _clean(values) -> list[str]:
    """Phrases normalisées, sans vide ni doublon (casse ignorée), ordre gardé."""
    seen = set()
    result = []
    for value in values:
        phrase = normalize_phrase(value)
        folded = phrase.casefold()
        if not phrase or folded in seen:
            continue
        seen.add(folded)
        result.append(phrase)
    return result


def _check(phrases, maximum) -> str | None:
    """Message d'erreur lisible, ou None si la liste est acceptable."""
    if len(phrases) > maximum:
        return f"{maximum} phrases au maximum."
    for phrase in phrases:
        if len(phrase) > MAX_LENGTH:
            return (f"« {phrase[:30]}… » est trop longue : "
                    f"{MAX_LENGTH} caractères au maximum par phrase.")
    return None


def _lenient(values, maximum) -> list[str]:
    """Lecture tolérante d'une valeur stockée : ce qui dépasse est ignoré
    plutôt que de priver l'équipier de tout son menu."""
    return [p for p in _clean(values) if len(p) <= MAX_LENGTH][:maximum]


# --- Liste d'équipe ---------------------------------------------------------

def team_phrases(config) -> list[str]:
    """Phrases d'équipe en vigueur (cf. docstring du module)."""
    value = (config or {}).get(TEAM_CONFIG_NAME)
    if value is None:
        return list(DEFAULT_TEAM)
    if not isinstance(value, str):
        return []
    return _lenient(value.splitlines(), MAX_TEAM)


def validate_team_text(value) -> dict:
    """Même contrat que ``utils.validate_and_transform_text`` :
    ``{"success": bool, "value": texte normalisé ou message d'erreur}``.
    Le texte enregistré garde une phrase par ligne, nettoyée."""
    phrases = _clean((value or "").splitlines())
    error = _check(phrases, MAX_TEAM)
    if error:
        return {"success": False, "value": error}
    return {"success": True, "value": "\n".join(phrases)}


# --- Phrases personnelles ---------------------------------------------------

class QuickRepliesError(ValueError):
    """Liste personnelle refusée ; ``message`` est affichable tel quel."""

    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


def validate_personal(values) -> list[str]:
    """Liste personnelle nettoyée, ou ``QuickRepliesError``."""
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise QuickRepliesError("invalid_quick_replies",
                                "La liste de phrases est invalide.")
    phrases = _clean(values)
    error = _check(phrases, MAX_PERSONAL)
    if error:
        raise QuickRepliesError("invalid_quick_replies", error)
    return phrases


def personal_phrases(stored) -> list[str]:
    """Phrases personnelles lues sur la fiche de l'équipier."""
    if not isinstance(stored, list):
        return []
    return _lenient([v for v in stored if isinstance(v, str)], MAX_PERSONAL)


def client_payload(config, stored_personal) -> dict:
    """Bloc ``quick_replies`` de l'état de messagerie envoyé à l'App."""
    return {
        "team": team_phrases(config),
        "personal": personal_phrases(stored_personal),
        "max_personal": MAX_PERSONAL,
        "max_length": MAX_LENGTH,
    }
