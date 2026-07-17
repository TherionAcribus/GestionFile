"""Diagnostic de configuration de la borne patient (cœur pur, sans base).

Ce module centralise la détection des problèmes de configuration qui rendent la
page ``/patient`` bancale ou inutilisable. Il est volontairement **pur** : il ne
prend que des objets « boutons » (avec ``id``, ``label``, ``is_parent``,
``is_present``, ``parent_button_id``) et un mapping de configuration optionnel.
Aucun import Flask/SQLAlchemy, afin d'être importable et testable isolément.

Deux consommateurs :

* la page borne (``routes/patient.py``) — pour griser les boutons parents sans
  sous-bouton (au lieu de planter sur ``children_buttons[0]``) ;
* la carte « Alertes » du tableau de bord admin — pour lister les problèmes.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Niveaux d'alerte (repris des classes Bootstrap : alert-danger / -warning / -info)
# ---------------------------------------------------------------------------
LEVEL_DANGER = "danger"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"


def find_empty_present_parents(buttons):
    """Boutons **parents affichés** qui n'ont **aucun sous-bouton affiché**.

    Un bouton parent (``is_parent``) n'ouvre un sous-menu que s'il possède au
    moins un enfant ``is_present``. Sinon, au clic, la borne n'a rien à montrer
    (et, historiquement, plantait sur ``children_buttons[0].shape``). On repère
    ces parents pour les griser côté borne et les signaler côté admin.

    ``buttons`` : itérable de l'**ensemble** des boutons (parents + enfants),
    pour pouvoir relier enfant → parent via ``parent_button_id``.
    """
    buttons = list(buttons)
    parents_with_present_child = {
        b.parent_button_id
        for b in buttons
        if b.parent_button_id is not None and b.is_present
    }
    return [
        b
        for b in buttons
        if b.is_parent and b.is_present and b.id not in parents_with_present_child
    ]


def has_any_usable_button(buttons):
    """``True`` s'il existe au moins un parcours possible pour le patient.

    Utilisable = un bouton-feuille affiché (activité directe) OU un bouton parent
    affiché possédant au moins un enfant affiché.
    """
    buttons = list(buttons)
    empty_parent_ids = {b.id for b in find_empty_present_parents(buttons)}

    for b in buttons:
        if not b.is_present:
            continue
        if not b.is_parent:
            return True  # feuille affichée (enfant ou bouton simple)
        if b.id not in empty_parent_ids:
            return True  # parent affiché avec des enfants affichés
    return False


def _picture_enabled_but_missing(config, display_key, picture_key):
    """Vrai si « afficher une image » est actif mais qu'aucune image n'est définie.

    Le gabarit masque désormais l'``<img>`` dans ce cas (plus d'image cassée),
    mais l'option reste incohérente : on la signale pour que l'admin choisisse
    une image ou désactive l'option."""
    if not config:
        return False
    display = config.get(display_key)
    picture = config.get(picture_key)
    return bool(display) and not (picture or "").strip()


def collect_patient_page_alerts(buttons, config=None):
    """Liste des problèmes de configuration de la page borne.

    Retourne une liste de dict ``{level, code, message, button_id?}`` triée par
    gravité (danger d'abord). ``config`` est un mapping type ``app.config`` ;
    s'il est absent, les contrôles qui en dépendent sont simplement ignorés.
    """
    buttons = list(buttons)
    alerts = []

    # 1) Aucun bouton utilisable : le patient ne peut rien sélectionner.
    if not has_any_usable_button(buttons):
        alerts.append({
            "level": LEVEL_DANGER,
            "code": "no_usable_button",
            "message": (
                "Aucun bouton n'est affiché sur la borne : le patient ne peut "
                "s'enregistrer pour aucune activité."
            ),
        })

    # 2) Boutons parents affichés mais sans sous-bouton : grisés sur la borne.
    for parent in find_empty_present_parents(buttons):
        alerts.append({
            "level": LEVEL_WARNING,
            "code": "empty_parent_button",
            "button_id": parent.id,
            "message": (
                f"Le bouton « {parent.label} » est un menu sans sous-bouton "
                "affiché : il apparaît grisé et reste inutilisable par le "
                "patient. Ajoutez-lui des sous-boutons ou masquez-le."
            ),
        })

    # 3) Images des boutons de validation activées mais non définies (option incohérente).
    if _picture_enabled_but_missing(
        config,
        "PAGE_PATIENT_BUTTON_PRINT_TICKET_DISPLAY_PICTURE",
        "PAGE_PATIENT_BUTTON_PRINT_TICKET_PICTURE",
    ):
        alerts.append({
            "level": LEVEL_INFO,
            "code": "print_button_picture_missing",
            "message": (
                "L'affichage d'une image sur le bouton « Imprimer » est activé "
                "mais aucune image n'est choisie : le bouton s'affiche sans "
                "image. Choisissez une image ou désactivez l'option."
            ),
        })

    if _picture_enabled_but_missing(
        config,
        "PAGE_PATIENT_BUTTON_CANCEL_DISPLAY_PICTURE",
        "PAGE_PATIENT_BUTTON_CANCEL_PICTURE",
    ):
        alerts.append({
            "level": LEVEL_INFO,
            "code": "cancel_button_picture_missing",
            "message": (
                "L'affichage d'une image sur le bouton « Annuler » est activé "
                "mais aucune image n'est choisie : le bouton s'affiche sans "
                "image. Choisissez une image ou désactivez l'option."
            ),
        })

    _order = {LEVEL_DANGER: 0, LEVEL_WARNING: 1, LEVEL_INFO: 2}
    alerts.sort(key=lambda a: _order.get(a["level"], 9))
    return alerts
