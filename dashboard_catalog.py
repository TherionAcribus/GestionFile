"""Catalogue des cartes du tableau de bord (noyau pur).

Source unique pour : le libellé et l'icône affichés (carte et gestionnaire —
qui montrait jusqu'ici les noms techniques « appschedule », « connection »…),
la description d'une carte, la page d'administration liée, la permission
requise pour la voir, et sa largeur dans la grille.

La permission est celle de la route de la carte (``require_permission_dashboard``) :
une carte que l'utilisateur ne peut pas charger n'est plus affichée du tout
(auparavant : une carte « Erreur de chargement » pour tout admin sans le
droit correspondant), le réordonnancement partiel des positions
(``reorder_positions``), la route de contenu de chaque carte et ses
déclencheurs temps réel.

Aucune dépendance Flask/SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    default: int
    min: int
    max: int
    help: str = ""


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
    # Route qui rend le contenu de la carte (chargement différé HTMX).
    data_url: str | None = None
    # Évènements HTMX temps réel qui rechargent la carte (en plus de « revealed once »).
    triggers: tuple[str, ...] = ()
    # Rechargement périodique, pour les cartes sans évènement temps réel ou
    # dont la source peut se taire (borne injoignable). Onglet masqué : pas de
    # requête.
    poll_seconds: int | None = None
    # Réglages éditables dans le gestionnaire (stockés dans card.settings).
    settings: tuple[SettingSpec, ...] = ()


CATALOG = {
    "today": CardInfo("Aujourd'hui", "bi-speedometer2",
                      "Les chiffres clés de la journée : attente, patients servis, comptoirs occupés.",
                      "/admin/stats", "queue", span="full", default_visible=True,
                      data_url="/admin/today/dashboard",
                      triggers=("refresh_queue_patient", "refresh_counter_dashboard"),
                      settings=(SettingSpec(
                          "wait_alert_minutes",
                          "Attente la plus longue en rouge à partir de (minutes)",
                          15, 1, 240),)),
    "alerts": CardInfo("Alertes", "bi-exclamation-triangle",
                       "Problèmes de configuration de la borne (n'apparaît que s'il y en a).",
                       "/admin/patient?tab=buttons", "patient",
                       data_url="/admin/alerts/dashboard", poll_seconds=120),
    "security": CardInfo("Sécurité", "bi-shield-lock",
                         "Vérifie que le mot de passe administrateur par défaut a été changé.",
                         "/admin/security", "security",
                         data_url="/admin/security/dashboard", poll_seconds=300),
    "queue": CardInfo("File d'attente", "bi-people",
                      "Patients en attente, appelés et au comptoir, en direct.",
                      "/admin/queue", "queue",
                      data_url="/admin/queue/dashboard",
                      triggers=("refresh_queue_patient",),
                      settings=(SettingSpec(
                          "max_rows", "Patients en attente affichés", 10, 3, 50,
                          "Au-delà, « … et N autres »."),)),
    "counter": CardInfo("Comptoirs", "bi-window-stack",
                        "Qui est connecté à quel comptoir, et avec quel patient.",
                        "/admin/counter", "counter",
                        data_url="/admin/counter/dashboard",
                        triggers=("refresh_counter_dashboard",)),
    "staff": CardInfo("Équipe", "bi-person-badge",
                      "Membres de l'équipe et comptoir occupé.",
                      "/admin/staff", "staff",
                      data_url="/admin/staff/dashboard",
                      triggers=("refresh_counter_dashboard",)),
    "button": CardInfo("Boutons de la borne", "bi-grid-3x3-gap",
                       "Activer ou désactiver rapidement un bouton de la borne.",
                       "/admin/patient?tab=buttons", "patient", span="wide",
                       data_url="/admin/button/dashboard", poll_seconds=60),
    "printer": CardInfo("Imprimante", "bi-printer",
                        "État de l'imprimante de la borne et derniers messages.",
                        "/admin/patient", "patient",
                        data_url="/admin/printer/dashboard",
                        triggers=("refresh_printer_dashboard",),
                        poll_seconds=60),
    "player": CardInfo("Musique", "bi-music-note-beamed",
                       "Lecteur Spotify de l'écran d'annonce.",
                       "/admin/music", "music_play",
                       data_url="/admin/music/dashboard", poll_seconds=30),
    "connection": CardInfo("Connexions", "bi-broadcast",
                           "Nombre d'appareils connectés en direct (bornes, écrans, comptoirs…).",
                           "/admin/app/connexion", "app",
                           data_url="/admin/communication/dashboard",
                           poll_seconds=60),
    "appschedule": CardInfo("Tâches planifiées", "bi-calendar-check",
                            "Prochaines tâches automatiques et échecs éventuels.",
                            "/admin/database", "schedule",
                            data_url="/admin/appschedule/dashboard",
                            poll_seconds=60),
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


def card_settings(name, raw):
    """Réglages effectifs d'une carte : valeurs par défaut du catalogue,
    surchargées par ``raw`` (dict JSON stocké) — valeurs hors bornes ou
    invalides ignorées."""
    out = {}
    raw = raw if isinstance(raw, dict) else {}
    for spec in card_info(name).settings:
        value = raw.get(spec.key, spec.default)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = spec.default
        out[spec.key] = value if spec.min <= value <= spec.max else spec.default
    return out


def validate_settings(name, raw):
    """Réglages soumis par le gestionnaire → dict propre à stocker.
    Lève ValueError (message lisible) sur clé inconnue ou valeur hors bornes."""
    if not isinstance(raw, dict):
        raise ValueError("Réglages invalides : objet attendu")
    specs = {spec.key: spec for spec in card_info(name).settings}
    clean = {}
    for key, value in raw.items():
        spec = specs.get(key)
        if spec is None:
            raise ValueError(f"Réglage inconnu : {key}")
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{spec.label} : nombre entier attendu")
        if not spec.min <= value <= spec.max:
            raise ValueError(f"{spec.label} : entre {spec.min} et {spec.max}")
        clean[key] = value
    return clean


@dataclass
class CardView:
    """Carte telle que vue par un administrateur (commune ou personnalisée)."""
    id: int
    name: str
    visible: bool
    position: int
    settings: dict | None = None


def apply_user_layout(cards, layout):
    """Vues des cartes pour un administrateur.

    ``cards`` : cartes communes triées (position, id) exposant id/name/visible/settings ;
    ``layout`` : ``{"order": [...], "hidden": [...]}`` ou None (→ vues identiques
    aux cartes communes). Avec disposition : les cartes citées dans ``order``
    d'abord, dans cet ordre, visibles sauf si dans ``hidden`` ; puis les cartes
    non citées (ajoutées au catalogue depuis), dans l'ordre commun, avec la
    visibilité par défaut du catalogue. Positions renumérotées 1..N.
    """
    views = []
    if not layout:
        for index, card in enumerate(cards, start=1):
            views.append(CardView(card.id, card.name, card.visible, index,
                                  getattr(card, "settings", None)))
        return views
    by_name = {card.name: card for card in cards}
    hidden = set(layout.get("hidden", []))
    seen = set()
    for name in layout.get("order", []):
        if name in by_name and name not in seen:
            seen.add(name)
            card = by_name[name]
            views.append(CardView(card.id, name, name not in hidden, 0,
                                  getattr(card, "settings", None)))
    for card in cards:
        if card.name not in seen:
            views.append(CardView(card.id, card.name,
                                  card_info(card.name).default_visible, 0,
                                  getattr(card, "settings", None)))
    for index, view in enumerate(views, start=1):
        view.position = index
    return views


def layout_from_order(ordered_names, visible_names):
    """Disposition personnelle à stocker à partir du gestionnaire :
    ``order`` = noms connus du catalogue, dédoublonnés, dans l'ordre ;
    ``hidden`` = ceux de ``order`` absents de ``visible_names``."""
    order = [name for name in dict.fromkeys(ordered_names) if name in CATALOG]
    visible = set(visible_names)
    return {"order": order, "hidden": [n for n in order if n not in visible]}


def validate_layout(raw):
    """Disposition lue en base → forme sûre (listes de str du catalogue,
    dédoublonnées, hidden ⊆ order). None/forme invalide → None."""
    if not isinstance(raw, dict):
        return None
    order, hidden = raw.get("order"), raw.get("hidden")
    if not isinstance(order, list) or not isinstance(hidden, list):
        return None
    order = [n for n in dict.fromkeys(order)
             if isinstance(n, str) and n in CATALOG]
    if not order:
        return None
    hidden = [n for n in dict.fromkeys(hidden) if n in order]
    return {"order": order, "hidden": hidden}


def default_layout():
    """Disposition d'usine : ``{name: (visible, position)}`` dans l'ordre du catalogue."""
    return {name: (info.default_visible, index)
            for index, (name, info) in enumerate(CATALOG.items(), start=1)}


def card_name_for_url(path):
    """Nom de la carte dont la route de contenu est ``path`` (None sinon)."""
    return next((name for name, info in CATALOG.items() if info.data_url == path), None)


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
