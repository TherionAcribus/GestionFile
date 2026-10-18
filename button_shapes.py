"""Formes des boutons de la borne patient, par niveau d'affichage.

La forme est enregistrée bouton par bouton (``Button.shape``). L'éditeur
visuel la présente en deux réglages distincts :

* ``page_patient_button_shape`` — boutons de l'accueil (sans parent) ;
* ``page_patient_children_button_shape`` — boutons de sous-activités
  (affichés après le choix d'un bouton parent).

Chaque réglage vaut ``circle`` ou ``square`` quand tous les boutons de son
niveau partagent cette forme, et ``mixed`` quand le mode avancé les a
différenciés. Module pur : aucune dépendance Flask/SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import Iterable

SHAPES = frozenset({"circle", "square"})
MIXED = "mixed"

HOME_SHAPE_KEY = "page_patient_button_shape"
CHILDREN_SHAPE_KEY = "page_patient_children_button_shape"
SHAPE_KEYS = (HOME_SHAPE_KEY, CHILDREN_SHAPE_KEY)


def shape_mode(shapes: Iterable[str | None], fallback: str | None = None) -> str | None:
    """Mode d'un groupe de boutons : la forme commune, sinon ``mixed``.

    Un groupe vide (aucun bouton de ce niveau) n'a pas de forme à refléter :
    la valeur déjà enregistrée (``fallback``) est conservée, ce qui garde le
    choix de l'utilisateur pour les boutons qu'il créera ensuite.
    """
    known = {shape for shape in shapes if shape in SHAPES}
    if not known:
        return fallback
    return next(iter(known)) if len(known) == 1 else MIXED


def shape_modes(rows: Iterable[tuple[int | None, str | None]],
                fallbacks: dict[str, str | None] | None = None) -> dict[str, str | None]:
    """Modes des deux niveaux à partir de couples ``(parent_button_id, shape)``."""
    fallbacks = fallbacks or {}
    home, children = [], []
    for parent_id, shape in rows:
        (home if parent_id is None else children).append(shape)
    return {
        HOME_SHAPE_KEY: shape_mode(home, fallbacks.get(HOME_SHAPE_KEY)),
        CHILDREN_SHAPE_KEY: shape_mode(children, fallbacks.get(CHILDREN_SHAPE_KEY)),
    }
