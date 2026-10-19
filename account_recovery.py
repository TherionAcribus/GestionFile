"""Récupération de compte — noyau pur (aucune dépendance Flask ni base).

Trois voies de récupération sans e-mail, proportionnées à un système de file
d'attente :

1. un administrateur réinitialise le mot de passe d'un autre compte, avec
   changement obligatoire à la prochaine connexion ;
2. la commande de secours ``python manage.py reset-password <identifiant>``,
   lancée depuis le terminal du conteneur (onglet *Terminal* de Coolify) ;
3. la variable d'environnement ``ADMIN_RESET_PASSWORD``, appliquée UNE seule
   fois au démarrage (conteneur ``init``).

Quatrième voie, si l'envoi d'e-mails est configuré : le lien « Mot de passe
oublié » (routes/password_reset.py), signé, valable une heure et à usage
unique.

Ce module ne contient que les décisions : génération d'un mot de passe
temporaire lisible, empreinte « déjà appliqué » de la variable, liste des
pages accessibles tant que le changement obligatoire n'est pas fait, jeton et
adresse du lien de réinitialisation.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlparse

from itsdangerous import BadSignature, URLSafeTimedSerializer

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


# ---------------------------------------------------------------------------
# Lien « Mot de passe oublié » (e-mail)
# ---------------------------------------------------------------------------

RESET_TOKEN_MAX_AGE = 3600  # secondes : le lien vaut une heure
_RESET_SALT = "gestionfile-password-reset-v1"


def password_fingerprint(password_hash: str | None) -> str:
    """Empreinte courte du hash de mot de passe courant.

    Glissée dans le jeton : dès que le mot de passe change (lien utilisé,
    changement volontaire, réinitialisation par un admin), l'empreinte ne
    correspond plus et le lien devient inutilisable — usage unique sans rien
    stocker en base.
    """
    return hashlib.sha256(("pwd:" + (password_hash or "")).encode("utf-8")).hexdigest()[:24]


def make_reset_token(secret_key: str, user_id: int, password_hash: str | None) -> str:
    serializer = URLSafeTimedSerializer(secret_key, salt=_RESET_SALT)
    return serializer.dumps({"u": int(user_id), "f": password_fingerprint(password_hash)})


def read_reset_token(secret_key: str, token: str | None, *,
                     max_age: int = RESET_TOKEN_MAX_AGE):
    """``(user_id, empreinte)`` d'un jeton valide et non expiré, sinon None."""
    if not token or len(token) > 512:
        return None
    serializer = URLSafeTimedSerializer(secret_key, salt=_RESET_SALT)
    try:
        data = serializer.loads(token, max_age=max_age)
    except BadSignature:  # inclut SignatureExpired
        return None
    if not isinstance(data, dict):
        return None
    user_id, fingerprint = data.get("u"), data.get("f")
    if not isinstance(user_id, int) or not isinstance(fingerprint, str):
        return None
    return user_id, fingerprint


def token_matches_password(fingerprint: str, password_hash: str | None) -> bool:
    return hmac.compare_digest(fingerprint, password_fingerprint(password_hash))


def normalize_base_url(value: str | None) -> str | None:
    """Adresse publique ``http(s)://hôte[:port]`` sans chemin, ou None."""
    value = (value or "").strip()
    if not value or len(value) > 200:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.username or parsed.password or any(c.isspace() for c in parsed.netloc):
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def reset_link(base_url: str, path: str) -> str:
    """Lien absolu vers ``path`` sur l'adresse publique enregistrée."""
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def reset_email_content(username: str, site_name: str, link: str,
                        max_age: int = RESET_TOKEN_MAX_AGE) -> tuple[str, str]:
    """``(sujet, corps texte)`` de l'e-mail « Mot de passe oublié »."""
    minutes = max_age // 60
    validity = "1 heure" if minutes == 60 else f"{minutes} minutes"
    subject = f"Réinitialisation de votre mot de passe — {site_name}"
    body = (
        f"Bonjour,\n\n"
        f"Une réinitialisation du mot de passe du compte « {username} » a été demandée "
        f"sur la file d'attente de {site_name}.\n\n"
        f"Pour choisir un nouveau mot de passe, ouvrez ce lien "
        f"(valable {validity}, utilisable une seule fois) :\n\n"
        f"{link}\n\n"
        f"Si vous n'êtes pas à l'origine de cette demande, ignorez ce message : "
        f"votre mot de passe actuel reste valable.\n"
    )
    return subject, body
