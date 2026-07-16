"""Politique minimale de mot de passe (point 3.4).

Module **pur** (aucune dépendance Flask) : validation d'un mot de passe au moment
de sa **création/modification** (ajout d'utilisateur, changement de mot de passe).
La connexion, elle, ne ré-applique pas la politique — elle vérifie seulement le
hash existant — mais durcir la fabrique de mots de passe fait partie du même
objectif « connexion ».

Politique volontairement *minimale* (pas de complexité imposée qui pousse aux
mots de passe faibles mémorisables) :

- longueur minimale (``MIN_LENGTH``) ;
- refus des mots de passe manifestement faibles / par défaut (denylist) ;
- interdiction d'un mot de passe égal (ou trivialement lié) au nom d'utilisateur.

``validate_password`` renvoie la **liste des problèmes** (vide = conforme), pour
afficher un message clair à l'administrateur qui crée le compte.
"""

from __future__ import annotations

MIN_LENGTH = 10

# Mots de passe notoirement faibles / par défaut, refusés quelle que soit la
# longueur. Comparaison insensible à la casse. Liste courte et ciblée : on ne
# cherche pas l'exhaustivité d'un dictionnaire, juste à barrer les évidences et
# les identifiants par défaut du projet.
_WEAK_PASSWORDS = {
    "admin",
    "administrator",
    "password",
    "motdepasse",
    "changeme",
    "changez_moi",
    "azerty",
    "azertyuiop",
    "qwerty",
    "123456",
    "1234567890",
    "0000000000",
    "gestionfile",
}


def validate_password(password: str | None, *, username: str | None = None) -> list[str]:
    """Renvoie la liste des non-conformités (vide si le mot de passe convient)."""
    problems: list[str] = []

    if not password:
        return ["Le mot de passe est obligatoire."]

    if len(password) < MIN_LENGTH:
        problems.append(
            f"Le mot de passe doit contenir au moins {MIN_LENGTH} caractères."
        )

    if password.strip() == "":
        problems.append("Le mot de passe ne peut pas être composé uniquement d'espaces.")

    normalized = password.strip().lower()
    if normalized in _WEAK_PASSWORDS:
        problems.append("Ce mot de passe est trop courant ; choisissez-en un autre.")

    if username:
        uname = username.strip().lower()
        if uname and normalized == uname:
            problems.append("Le mot de passe ne doit pas être identique au nom d'utilisateur.")

    return problems


def is_valid_password(password: str | None, *, username: str | None = None) -> bool:
    return not validate_password(password, username=username)
