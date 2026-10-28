"""Textes des notifications de l'App Comptoir, réglables dans l'administration.

Catalogue pur (sans Flask ni base), source unique de vérité pour :

- la liste des messages proposés dans Admin › Application › Notifications ;
- la clé de configuration de chacun (``app_notif_<id>``) et son texte
  d'origine (repli quand le champ est vide ou absent) ;
- les balises autorisées dans chaque message ({N} numéro du patient…) ;
- qui fabrique le texte : le **serveur** (notifications poussées par
  ``communication.send_app_notification``) ou l'**App** (alertes locales :
  connexion perdue, rappel de validation…). Les textes « App » lui sont
  transmis dans l'état du comptoir (``/app/counter/state``) ; l'App garde ses
  propres textes d'origine pour rester correcte face à un serveur ancien.

Les textes d'origine doivent rester identiques à ceux de
``App_Comptoir/notification_texts.py`` (un test le vérifie).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Préfixe des clés de configuration de ce catalogue.
KEY_PREFIX = "app_notif_"

#: Longueur maximale d'un message : une notification se lit d'un coup d'œil.
MAX_LENGTH = 300

#: Balises possibles et leur signification (boutons d'insertion de l'admin).
MARKER_LABELS = {
    "N": "Numéro du patient",
    "A": "Activité",
    "M": "Membre de l'équipe",
    "S": "Secondes restantes",
    "B": "Borne",
    "E": "Détail de l'incident",
}

# Qui fabrique le texte.
APP = "app"
SERVER = "server"
BOTH = "both"


@dataclass(frozen=True)
class NotificationText:
    message_id: str
    group: str
    label: str
    when: str
    default: str
    markers: str = ""
    rendered_by: str = APP

    @property
    def key(self) -> str:
        return KEY_PREFIX + self.message_id

    @property
    def config_name(self) -> str:
        return self.key.upper()


#: Groupes de l'onglet d'administration : (id, titre, icône).
GROUPS = (
    ("patients", "Patients et appels", "bi-person-badge"),
    ("validation", "Rappel de validation", "bi-hourglass-split"),
    ("connection", "Connexion au serveur", "bi-wifi"),
    ("kiosk", "Papier et bornes", "bi-printer"),
    ("actions", "Actions au comptoir", "bi-hand-index"),
)

CATALOGUE: tuple[NotificationText, ...] = (
    # --- Patients et appels -------------------------------------------------
    NotificationText(
        "new_patient", "patients", "Patient appelé",
        "Vous appelez un patient (bouton « Suivant », liste, raccourci).",
        "Nouveau patient : {N} pour '{A}'", "NA"),
    NotificationText(
        "autocalling", "patients", "Appel automatique",
        "Un patient vous est attribué par l'appel automatique.",
        "Appel automatique du patient {N} pour '{A}'", "NA"),
    NotificationText(
        "activity", "patients", "Nouveau patient pour une activité suivie",
        "Un patient s'inscrit à une activité dont la notification est activée "
        "(Activités › notification).",
        "{A} : {N}", "NA", SERVER),
    NotificationText(
        "patient_taken", "patients", "Patient appelé par un autre comptoir",
        "Le patient que vous appeliez vient d'être pris par un autre comptoir.",
        "Le patient {N} vient d'être appelé par un autre comptoir.", "N", SERVER),
    NotificationText(
        "patient_taken_queue", "patients", "Patient de la liste déjà pris en charge",
        "Vous agissez sur un patient de la liste qu'un autre comptoir a déjà pris.",
        "Patient déjà pris en charge par un autre comptoir."),
    NotificationText(
        "patient_redirected", "patients", "Patient redirigé vers vous",
        "Un collègue vous adresse un patient (« Remettre en attente pour… »).",
        "Le patient {N} est redirigé vers vous.", "N", SERVER),
    # --- Rappel de validation -----------------------------------------------
    NotificationText(
        "please_validate", "validation", "Patient non validé",
        "Le patient appelé n'a pas été validé après le délai réglé dans les "
        "préférences de l'App.",
        "Pensez à valider votre patient afin de vider l'écran d'affichage.", "N"),
    NotificationText(
        "auto_release_warning", "validation", "Avant la libération automatique",
        "Libération automatique activée (onglet Général) : le patient va quitter "
        "l'écran d'appel.",
        "Le patient sera retiré automatiquement de l'écran d'appel dans {S} "
        "secondes. Validez-le ou prolongez l'appel.", "NS"),
    # --- Connexion ------------------------------------------------------------
    NotificationText(
        "server_unreachable", "connection", "Serveur inaccessible",
        "L'App n'arrive pas à joindre le serveur.",
        "Le serveur est inaccessible."),
    NotificationText(
        "realtime_lost", "connection", "Connexion temps réel perdue",
        "La liaison temps réel est coupée (la liste des patients ne se met plus "
        "à jour toute seule).",
        "La connexion temps réel a été perdue. Tentative de reconnexion... La "
        "liste des patients ne s'affichera plus en temps réel, mais les boutons "
        "fonctionnent toujours."),
    NotificationText(
        "realtime_restored", "connection", "Connexion temps réel rétablie",
        "La liaison temps réel revient après une coupure.",
        "La connexion temps réel est (r)établie !"),
    NotificationText(
        "disconnected_by_user", "connection", "Déconnexion par un autre poste",
        "Quelqu'un vous a retiré du comptoir (administration ou autre poste).",
        "Vous avez été déconnecté par {M}.", "M"),
    # --- Papier et bornes ----------------------------------------------------
    NotificationText(
        "low_paper", "kiosk", "Papier bientôt épuisé",
        "L'imprimante de la borne signale la fin du rouleau, ou un collègue "
        "active le bouton papier.",
        "On est quasiment au bout du rouleau", "", BOTH),
    NotificationText(
        "no_paper", "kiosk", "Plus de papier",
        "L'imprimante de la borne n'a plus de papier.",
        "Il n'y a plus de papier dans la borne !", "", SERVER),
    NotificationText(
        "paper_ok", "kiosk", "Papier rechargé",
        "Le papier a été remis (imprimante ou bouton papier).",
        "Une gentille personne a remis du papier", "", BOTH),
    NotificationText(
        "printer_error", "kiosk", "Erreur d'impression",
        "Un ticket n'a pas pu être imprimé ; {E} décrit l'incident.",
        "Erreur d'impression : {E}", "E", SERVER),
    NotificationText(
        "patient_waiting", "kiosk", "Patient qui attend à la borne",
        "Après un échec d'impression, le patient a demandé le personnel.",
        "Un patient attend à la borne : {E}", "E", SERVER),
    NotificationText(
        "kiosk_offline", "kiosk", "Borne injoignable",
        "Une borne ne donne plus de nouvelles.",
        "Borne {B} injoignable : aucun contact récent.", "B", SERVER),
    NotificationText(
        "kiosk_online", "kiosk", "Borne de nouveau en ligne",
        "Une borne injoignable répond à nouveau.",
        "Borne {B} de nouveau en ligne.", "B", SERVER),
    # --- Actions au comptoir -------------------------------------------------
    NotificationText(
        "not_identified", "actions", "Personne n'est identifié au comptoir",
        "Une action est tentée alors qu'aucun membre de l'équipe n'est connecté.",
        "Identifiez-vous sur un comptoir pour agir sur la file."),
    NotificationText(
        "action_busy", "actions", "Action déjà en cours",
        "Un second clic arrive avant la fin de l'action précédente.",
        "Une action est déjà en cours…"),
)

BY_ID = {item.message_id: item for item in CATALOGUE}
BY_KEY = {item.key: item for item in CATALOGUE}
KEYS = frozenset(BY_KEY)


def is_notification_key(key) -> bool:
    return isinstance(key, str) and key in BY_KEY


def text_for(config, message_id) -> str:
    """Modèle en vigueur : la valeur réglée, ou le texte d'origine si elle
    est vide/absente (une notification n'est jamais vide)."""
    item = BY_ID[message_id]
    value = (config or {}).get(item.config_name)
    if isinstance(value, str) and value.strip():
        return value
    return item.default


_MARKER = re.compile(r"\{([A-Za-z])\}")


def render(template, **values) -> str:
    """Remplace les balises ``{X}`` fournies ; les autres restent telles quelles."""
    def _sub(match):
        letter = match.group(1).upper()
        if letter in values:
            value = values[letter]
            return "" if value is None else str(value)
        return match.group(0)
    return _MARKER.sub(_sub, template or "")


def render_message(config, message_id, **values) -> str:
    return render(text_for(config, message_id), **values)


def client_texts(config) -> dict:
    """Modèles fabriqués par l'App : {id: modèle} (envoyés dans l'état du
    comptoir). Le texte d'origine est envoyé quand rien n'est réglé."""
    return {item.message_id: text_for(config, item.message_id)
            for item in CATALOGUE if item.rendered_by in (APP, BOTH)}


def _markers_hint(letters) -> str:
    if not letters:
        return "Ce message n'accepte aucune balise."
    return "Balises possibles : " + ", ".join("{" + c + "}" for c in letters) + "."


def validate(key, value) -> dict:
    """Même contrat que ``utils.validate_and_transform_text`` :
    ``{"success": bool, "value": texte normalisé ou message d'erreur}``.

    Vide = revenir au texte d'origine. Les balises en minuscules sont remises
    en majuscules ; une balise non prévue pour ce message est refusée (elle
    s'afficherait telle quelle au comptoir)."""
    item = BY_KEY.get(key)
    if item is None:
        return {"success": True, "value": value}
    text = (value or "").strip()
    if len(text) > MAX_LENGTH:
        return {"success": False,
                "value": f"Le message ne doit pas dépasser {MAX_LENGTH} caractères."}
    allowed = set(item.markers)
    unknown = sorted({m.upper() for m in _MARKER.findall(text)} - allowed)
    if unknown or re.search(r"\{[^{}]{2,}\}|\{\}", text):
        return {"success": False, "value": "Certaines balises sont incorrectes. "
                + _markers_hint(item.markers)}
    text = _MARKER.sub(lambda m: "{" + m.group(1).upper() + "}", text)
    return {"success": True, "value": text}


def admin_groups(config) -> list[dict]:
    """Données de l'onglet d'administration, groupées."""
    groups = []
    for group_id, title, icon in GROUPS:
        items = []
        for item in CATALOGUE:
            if item.group != group_id:
                continue
            value = (config or {}).get(item.config_name)
            items.append({
                "key": item.key,
                "label": item.label,
                "when": item.when,
                "value": value if isinstance(value, str) and value.strip() else item.default,
                "default": item.default,
                "markers": [("{" + c + "}", MARKER_LABELS[c]) for c in item.markers],
                "rendered_by": item.rendered_by,
            })
        groups.append({"id": group_id, "title": title, "icon": icon, "messages": items})
    return groups
