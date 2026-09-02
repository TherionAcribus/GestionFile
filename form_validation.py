"""Validation déclarative des formulaires — cœur **pur** (Phase 8, point 5).

Aucune dépendance à Flask, à SQLAlchemy ni à la base : ce module ne manipule que
des dictionnaires, ce qui le rend testable sans application ni serveur.

Le problème qu'il résout : chaque route d'administration refaisait ses contrôles
à la main, avec des formulations différentes pour la même règle ::

    if request.form.get('name') == '':      # ici
    if not name:                            # là
    if not initials:                        # ailleurs, message encore différent

…et rien ne garantissait qu'un champ obligatoire le reste après une refonte du
gabarit. On déclare désormais le formulaire une fois, et la validation devient
une donnée plutôt que du code recopié.

Usage typique dans une vue ::

    SCHEMA = (
        Champ("name", obligatoire=True, libelle="Le nom", longueur_max=100),
        Champ("activities", type=LISTE_ENTIERS),
    )
    valeurs, erreurs = valider(extraire(SCHEMA, request.form.get, request.form.getlist),
                               SCHEMA)
    if erreurs:
        return display_toast(success=False, message=erreurs[0])
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

# Types de champ reconnus.
TEXTE = "texte"
ENTIER = "entier"
BOOLEEN = "booleen"
LISTE_ENTIERS = "liste_entiers"

_TYPES = {TEXTE, ENTIER, BOOLEEN, LISTE_ENTIERS}

#: Valeurs de formulaire HTML comptant pour « vrai » (cases à cocher, switches).
_VRAI = {"true", "on", "1", "yes", "oui"}


@dataclass(frozen=True)
class Champ:
    """Déclaration d'un champ de formulaire.

    :param nom: clé dans le formulaire HTML.
    :param obligatoire: refuse une valeur absente ou vide (après strip).
    :param libelle: nom lisible utilisé dans les messages d'erreur ; à défaut, ``nom``.
    :param type: TEXTE, ENTIER, BOOLEEN ou LISTE_ENTIERS.
    :param longueur_max: longueur maximale pour un TEXTE (bornée côté serveur,
        indépendamment du ``maxlength`` du gabarit, que le client peut ignorer).
    :param choix: valeurs autorisées ; toute autre valeur est refusée.
    :param defaut: valeur retenue quand le champ est absent et non obligatoire.
    :param normaliser: transformation appliquée à la valeur texte avant contrôle
        (par exemple ``str.upper`` pour des initiales).
    """

    nom: str
    obligatoire: bool = False
    libelle: str | None = None
    type: str = TEXTE
    longueur_max: int | None = None
    choix: Sequence[Any] | None = None
    defaut: Any = None
    normaliser: Callable[[str], str] | None = None

    def __post_init__(self):
        if self.type not in _TYPES:
            raise ValueError(f"Type de champ inconnu : {self.type!r}")

    @property
    def intitule(self) -> str:
        return self.libelle or self.nom


@dataclass
class Resultat:
    """Issue d'une validation : les valeurs converties et la liste des erreurs."""

    valeurs: dict = field(default_factory=dict)
    erreurs: list = field(default_factory=list)

    def __bool__(self):
        """Vrai quand le formulaire est valide (aucune erreur)."""
        return not self.erreurs

    def __iter__(self):
        """Permet ``valeurs, erreurs = valider(...)``."""
        return iter((self.valeurs, self.erreurs))


def extraire(schema: Iterable[Champ], get, getlist=None) -> dict:
    """Construit le dictionnaire brut attendu par :func:`valider`.

    ``get`` et ``getlist`` sont les accesseurs du formulaire (typiquement
    ``request.form.get`` et ``request.form.getlist``). Les passer en paramètres
    garde ce module indépendant de Flask.
    """
    brut = {}
    for champ in schema:
        if champ.type == LISTE_ENTIERS:
            brut[champ.nom] = list(getlist(champ.nom)) if getlist else []
        else:
            brut[champ.nom] = get(champ.nom)
    return brut


def valider(donnees: Mapping, schema: Iterable[Champ]) -> Resultat:
    """Valide ``donnees`` contre ``schema``.

    Renvoie un :class:`Resultat` : ``valeurs`` ne contient que les champs
    correctement convertis, ``erreurs`` la liste des messages (un par problème,
    dans l'ordre du schéma) prêts à être affichés.

    Ne lève jamais : une entrée aberrante produit une erreur, pas une exception.
    """
    resultat = Resultat()

    for champ in schema:
        brut = donnees.get(champ.nom)

        if champ.type == LISTE_ENTIERS:
            valeurs, erreur = _convertir_liste_entiers(champ, brut)
            if erreur:
                resultat.erreurs.append(erreur)
            else:
                resultat.valeurs[champ.nom] = valeurs
            continue

        if champ.type == BOOLEEN:
            resultat.valeurs[champ.nom] = _convertir_booleen(brut, champ.defaut)
            continue

        texte = "" if brut is None else str(brut).strip()
        if champ.normaliser and texte:
            texte = champ.normaliser(texte)

        if not texte:
            if champ.obligatoire:
                resultat.erreurs.append(f"{champ.intitule} est obligatoire.")
            else:
                resultat.valeurs[champ.nom] = champ.defaut
            continue

        if champ.type == ENTIER:
            try:
                valeur = int(texte)
            except (TypeError, ValueError):
                resultat.erreurs.append(f"{champ.intitule} doit être un nombre entier.")
                continue
        else:
            if champ.longueur_max is not None and len(texte) > champ.longueur_max:
                resultat.erreurs.append(
                    f"{champ.intitule} ne doit pas dépasser {champ.longueur_max} caractères."
                )
                continue
            valeur = texte

        if champ.choix is not None and valeur not in champ.choix:
            resultat.erreurs.append(f"{champ.intitule} a une valeur non autorisée.")
            continue

        resultat.valeurs[champ.nom] = valeur

    return resultat


def _convertir_booleen(brut, defaut):
    if brut is None:
        return bool(defaut)
    if isinstance(brut, bool):
        return brut
    return str(brut).strip().lower() in _VRAI


def _convertir_liste_entiers(champ, brut):
    if not brut:
        if champ.obligatoire:
            return None, f"{champ.intitule} est obligatoire."
        return [], None

    valeurs = []
    for element in brut:
        try:
            valeurs.append(int(str(element).strip()))
        except (TypeError, ValueError):
            return None, f"{champ.intitule} contient une valeur invalide."
    return valeurs, None
