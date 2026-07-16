"""Journal d'audit des connexions (point 3.4).

Module **pur** : construit une ligne d'audit normalisée pour chaque tentative de
connexion — réussie, refusée ou bloquée par la limitation — **sans jamais**
inclure de secret (ni mot de passe, ni cookie, ni jeton). La route de connexion
émet ensuite cette ligne via le logger applicatif.

On ne journalise que des métadonnées non sensibles : issue, nom d'utilisateur
*revendiqué* (utile pour repérer un balayage de comptes ; ce n'est pas un
secret), adresse IP, une empreinte tronquée du User-Agent, et le cas échéant le
délai imposé. Aucune donnée de mot de passe ne transite par ici : la fonction
n'accepte tout simplement pas de champ mot de passe.
"""

from __future__ import annotations

# Issues normalisées.
OUTCOME_SUCCESS = "success"      # authentification réussie
OUTCOME_FAILURE = "failure"      # identifiants invalides (utilisateur inconnu OU mauvais mot de passe)
OUTCOME_BLOCKED = "blocked"      # refus avant vérification : limitation de tentatives active

_VALID_OUTCOMES = {OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_BLOCKED}

_MAX_UA_LEN = 120
_MAX_USERNAME_LEN = 64


def _sanitize(value, max_len: int) -> str:
    """Aplati une valeur libre en une chaîne courte et mono-ligne.

    Retire les retours à la ligne (empêche l'injection de fausses lignes de log)
    et borne la longueur pour ne pas laisser un client gonfler le journal.
    """
    if value is None:
        return "-"
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return "-"
    if len(text) > max_len:
        text = text[:max_len] + "…"
    return text


def build_login_audit(
    outcome: str,
    *,
    username=None,
    ip=None,
    user_agent=None,
    retry_after: float | None = None,
) -> dict:
    """Construit l'enregistrement d'audit (dict de champs non sensibles)."""
    if outcome not in _VALID_OUTCOMES:
        outcome = OUTCOME_FAILURE
    record = {
        "event": "login",
        "outcome": outcome,
        "username": _sanitize(username, _MAX_USERNAME_LEN),
        "ip": _sanitize(ip, 64),
        "user_agent": _sanitize(user_agent, _MAX_UA_LEN),
    }
    if retry_after and retry_after > 0:
        # Arrondi entier : la précision sub-seconde n'apporte rien au journal.
        record["retry_after_s"] = int(retry_after)
    return record


def format_login_audit(record: dict) -> str:
    """Rend l'enregistrement en une ligne ``clé=valeur`` stable et lisible."""
    parts = [f"{k}={record[k]}" for k in record]
    return "auth " + " ".join(parts)
