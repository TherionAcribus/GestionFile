"""Catalogue des cartes du tableau de bord (noyau pur).

Source unique pour : le libellé et l'icône affichés (carte et gestionnaire —
qui montrait jusqu'ici les noms techniques « appschedule », « connection »…),
la description d'une carte, la page d'administration liée, la permission
requise pour la voir, et sa largeur dans la grille.

La permission est celle de la route de la carte (``require_permission_dashboard``) :
une carte que l'utilisateur ne peut pas charger n'est plus affichée du tout
(auparavant : une carte « Erreur de chargement » pour tout admin sans le
droit correspondant), et le réordonnancement partiel des positions
(``reorder_positions``).

Aucune dépendance Flask/SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CardInfo:
    label: str
    icon: str
    description: str
    url: str | None
    permission: str
    # « full » : toute la largeur ; « wide » : deux colonnes ; « normal ».
    span: str = "normal"
    # Carte affichée par défaut lorsqu'elle est ajoutée à une installation.
    default_visible: bool = False


CATALOG = {
    "today": CardInfo("Aujourd'hui", "bi-speedometer2",
                      "Les chiffres clés de la journée : attente, patients servis, comptoirs occupés.",
                      "/admin/stats", "queue", span="full", default_visible=True),
    "alerts": CardInfo("Alertes", "bi-exclamation-triangle",
                       "Problèmes de configuration de la borne (n'apparaît que s'il y en a).",
                       "/admin/patient?tab=buttons", "patient"),
    "security": CardInfo("Sécurité", "bi-shield-lock",
                         "Vérifie que le mot de passe administrateur par défaut a été changé.",
                         "/admin/security", "security"),
    "queue": CardInfo("File d'attente", "bi-people",
                      "Patients en attente, appelés et au comptoir, en direct.",
                      "/admin/queue", "queue"),
    "counter": CardInfo("Comptoirs", "bi-window-stack",
                        "Qui est connecté à quel comptoir, et avec quel patient.",
                        "/admin/counter", "counter"),
    "staff": CardInfo("Équipe", "bi-person-badge",
                      "Membres de l'équipe et comptoir occupé.",
                      "/admin/staff", "staff"),
    "button": CardInfo("Boutons de la borne", "bi-grid-3x3-gap",
                       "Activer ou désactiver rapidement un bouton de la borne.",
                       "/admin/patient?tab=buttons", "patient", span="wide"),
    "printer": CardInfo("Imprimante", "bi-printer",
                        "État de l'imprimante de la borne et derniers messages.",
                        "/admin/patient", "patient"),
    "player": CardInfo("Musique", "bi-music-note-beamed",
                       "Lecteur Spotify de l'écran d'annonce.",
                       "/admin/music", "music_play"),
    "connection": CardInfo("Connexions", "bi-broadcast",
                           "Nombre d'appareils connectés en direct (bornes, écrans, comptoirs…).",
                           "/admin/app/connexion", "app"),
    "appschedule": CardInfo("Tâches planifiées", "bi-calendar-check",
                            "Prochaines tâches automatiques et échecs éventuels.",
                            "/admin/database", "schedule"),
}


def card_info(name) -> CardInfo:
    """Informations d'une carte (repli lisible pour une carte inconnue)."""
    return CATALOG.get(name) or CardInfo(str(name), "bi-square", "", None, str(name))


def visible_cards(cards, can):
    """Cartes visibles que l'utilisateur a le droit de charger.

    ``cards`` : objets exposant ``name`` et ``visible`` ; ``can(resource)``
    indique si l'utilisateur a la permission.
    """
    return [card for card in cards
            if card.visible and can(card_info(card.name).permission)]


def missing_cards(existing_names):
    """Cartes du catalogue absentes de la base (à créer), dans l'ordre du catalogue."""
    existing = set(existing_names)
    return [name for name in CATALOG if name not in existing]


def reorder_positions(cards, ordered_ids):
    """Nouvelles positions après un réordonnancement partiel.

    ``cards`` : objets exposant ``id`` et ``position`` (toutes les cartes) ;
    ``ordered_ids`` : identifiants dans l'ordre souhaité (sous-ensemble, par
    ex. les seules cartes visibles par l'administrateur qui a glissé).
    Renumérote d'abord toutes les cartes 1..N (ordre position puis id, ce qui
    absorbe les doublons et les positions ≤ 0), puis répartit les cartes citées
    dans les créneaux qu'elles occupaient, dans l'ordre demandé : les cartes
    non citées ne bougent pas. Les identifiants inconnus sont ignorés, les
    doublons dédoublonnés. Retourne ``{id: position}``.
    """
    ranked = sorted(cards, key=lambda c: (c.position, c.id))
    positions = {c.id: index for index, c in enumerate(ranked, start=1)}
    cited = [i for i in dict.fromkeys(ordered_ids) if i in positions]
    slots = sorted(positions[i] for i in cited)
    positions.update(zip(cited, slots))
    return positions
