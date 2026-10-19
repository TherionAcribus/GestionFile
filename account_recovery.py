"""Récupération de compte — noyau pur (aucune dépendance Flask ni base).

Trois voies de récupération sans e-mail, proportionnées à un système de file
d'attente :

1. un administrateur réinitialise le mot de passe d'un autre compte, avec
   changement obligatoire à la prochaine connexion ;
2. la commande de secours ``python manage.py reset-password <identifiant>``,
   lancée depuis le terminal du conteneur (onglet *Terminal* de Coolify) ;
3. la variable d'environnement ``ADMIN_RESET_PASSWORD``, appliquée UNE seule
   fois au démarrage (conteneur ``init``).

Ce module ne contient que les décisions : génération d'un mot de passe
temporaire lisible, empreinte « déjà appliqué » de la variable, et liste des
pages accessibles tant que le changement obligatoire n'est pas fait.
"""

from __future__ import annotations

import hashlib
import secrets

# Alphabet sans caractères ambigus à la lecture ou à la dictée
# (pas de 0/o, 1/l/i) : le mot de passe temporaire est souvent recopié à la
# main depuis un terminal ou dicté au téléphone.
_TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
_TEMP_GROUPS = 3
_TEMP_GROUP_LEN = 4

ENV_RESET_VARIABLE = "ADMIN_RESET_PASSWORD"

# Préfixe de l'empreinte : la distingue de tout autre usage de SHA-256.
_MARKER_PREFIX = "gestionfile-env-reset:v1:"

# Endpoints accessibles tant que l'utilisateur doit changer son mot de passe.
# Tout le reste de l'administration le renvoie vers la page de changement.
MUST_CHANGE_ALLOWED_ENDPOINTS = frozenset({
    "admin_security.account_password",
    "static",
})


def generate_temporary_password() -> str:
    """Mot de passe temporaire lisible, ex. ``kfr7-m2pq-x9tw``.

    14 caractères (≥ politique de 10), ~60 bits d'aléa : largement suffisant
    pour un mot de passe qui doit être changé à la connexion suivante.
    """
    groups = (
        "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(_TEMP_GROUP_LEN))
        for _ in range(_TEMP_GROUPS)
    )
    return "-".join(groups)


def env_reset_marker(value: str | None) -> str | None:
    """Empreinte d'une valeur de ``ADMIN_RESET_PASSWORD`` (None si vide).

    Stockée sur le compte après application : tant que la variable garde la
    même valeur, les redémarrages suivants ne réinitialisent plus le compte
    (sinon le nouveau mot de passe choisi par l'utilisateur serait écrasé à
    chaque redéploiement). L'empreinte ne protège qu'un mot de passe
    temporaire, déjà invalidé par le changement obligatoire.
    """
    value = (value or "").strip()
    if not value:
        return None
    return hashlib.sha256((_MARKER_PREFIX + value).encode("utf-8")).hexdigest()


def env_reset_needed(value: str | None, stored_marker: str | None) -> bool:
    """Vrai si la variable est renseignée et pas encore appliquée à ce compte."""
    marker = env_reset_marker(value)
    return marker is not None and marker != stored_marker


def is_allowed_while_must_change(endpoint: str | None) -> bool:
    """Vrai si ``endpoint`` reste accessible avant le changement obligatoire."""
    return endpoint in MUST_CHANGE_ALLOWED_ENDPOINTS
