"""Source de vérité unique des permissions de rôle (point 1.5).

Historiquement, le formulaire de création de rôle listait « à la main » un jeu de
permissions qui avait divergé du modèle : il proposait ``admin_music`` et
``admin_dashboard`` (colonnes supprimées par la migration
``abc04012148c``) et **omettait** les permissions réellement présentes
(``music_play``, ``music_options``, ``staff``, ``phone``, ``announce``,
``patient``, ``gallery``…). Résultat : des cases sans effet et des domaines non
attribuables depuis l'IHM.

Ce module centralise **la** définition des permissions. Le modèle ``Role`` (dans
``models.py``), les formulaires d'ajout/édition de rôle et les gardes
``require_permission('<resource>')`` doivent tous s'accorder avec cette liste ;
des tests statiques (``tests/test_permissions_registry.py``) échouent sinon.

Conception volontairement **sans dépendance** (pas d'import de ``models`` ni de
Flask) pour rester importable dans des tests unitaires purs, sans MySQL.

Le modèle reste **booléen** (une colonne ``admin_<resource>`` par domaine). La
séparation lecture/écriture, si elle est un jour souhaitée, relèvera d'une
migration ultérieure et pourra réutiliser ce registre (ajout d'un champ
``actions`` par entrée) sans casser l'existant.
"""

from collections import OrderedDict
from dataclasses import dataclass


# Niveaux de risque, du plus élevé au plus faible. Purement indicatif dans l'IHM
# (tri/badge) : ne modifie **pas** la décision d'autorisation, qui reste binaire.
RISK_HIGH = "high"
RISK_MEDIUM = "medium"
RISK_LOW = "low"

RISK_LABELS = {
    RISK_HIGH: "Élevé",
    RISK_MEDIUM: "Modéré",
    RISK_LOW: "Faible",
}


@dataclass(frozen=True)
class Permission:
    """Une permission de domaine administratif.

    - ``resource`` : nom technique employé dans les gardes
      ``require_permission('<resource>')`` et ``user_has_permission(user,
      '<resource>')``. La colonne booléenne correspondante du modèle ``Role``
      est ``admin_<resource>`` (cf. :pyattr:`field`).
    - ``label`` : libellé français affiché dans l'IHM.
    - ``description`` : phrase courte expliquant l'étendue de l'accès.
    - ``category`` : regroupement pour l'affichage du formulaire.
    - ``risk`` : niveau de risque indicatif (``RISK_*``).
    """

    resource: str
    label: str
    description: str
    category: str
    risk: str = RISK_LOW

    @property
    def field(self) -> str:
        """Nom de la colonne booléenne dans le modèle ``Role``."""
        return f"admin_{self.resource}"


# Ordre = ordre d'affichage dans le formulaire (les catégories apparaissent dans
# l'ordre de première rencontre). Chaque ``resource`` correspond EXACTEMENT à une
# colonne ``admin_<resource>`` du modèle Role et à des gardes du même nom.
PERMISSIONS = [
    # --- Système (accès sensibles) ---
    Permission(
        "security",
        "Sécurité",
        "Gestion des utilisateurs, des rôles et des permissions.",
        "Système",
        RISK_HIGH,
    ),
    Permission(
        "app",
        "Application & sauvegardes",
        "Configuration de l'application comptoir, sauvegardes et restauration de la base.",
        "Système",
        RISK_HIGH,
    ),
    Permission(
        "options",
        "Options & tableau de bord",
        "Réglages généraux et configuration du tableau de bord.",
        "Système",
        RISK_MEDIUM,
    ),
    # --- Exploitation (usage quotidien de la file) ---
    Permission(
        "queue",
        "File d'attente",
        "Supervision et gestion de la file d'attente.",
        "Exploitation",
        RISK_MEDIUM,
    ),
    Permission(
        "counter",
        "Comptoirs",
        "Configuration et activation des comptoirs.",
        "Exploitation",
        RISK_MEDIUM,
    ),
    Permission(
        "staff",
        "Équipe",
        "Gestion des membres de l'équipe et de leurs activités.",
        "Exploitation",
        RISK_LOW,
    ),
    # --- Configuration de la file ---
    Permission(
        "activity",
        "Activités",
        "Gestion des activités (actes) proposées.",
        "Configuration",
        RISK_MEDIUM,
    ),
    Permission(
        "algo",
        "Algorithme de priorité",
        "Règles de priorité d'appel des patients.",
        "Configuration",
        RISK_MEDIUM,
    ),
    Permission(
        "schedule",
        "Horaires",
        "Plages horaires d'ouverture des activités.",
        "Configuration",
        RISK_LOW,
    ),
    Permission(
        "translation",
        "Traductions",
        "Traductions des textes et libellés.",
        "Configuration",
        RISK_LOW,
    ),
    # --- Affichages publics ---
    Permission(
        "patient",
        "Affichage patient",
        "Personnalisation de l'écran patient.",
        "Affichage",
        RISK_LOW,
    ),
    Permission(
        "announce",
        "Écran d'annonce",
        "Personnalisation et contenu de l'écran d'annonce.",
        "Affichage",
        RISK_LOW,
    ),
    Permission(
        "phone",
        "Affichage téléphone",
        "Personnalisation de l'affichage téléphone.",
        "Affichage",
        RISK_LOW,
    ),
    Permission(
        "gallery",
        "Galerie média",
        "Gestion des médias de la galerie diffusée.",
        "Affichage",
        RISK_LOW,
    ),
    # --- Musique ---
    Permission(
        "music_play",
        "Musique — lecture",
        "Pilotage de la lecture musicale (lecture, pause, volume, playlists).",
        "Musique",
        RISK_LOW,
    ),
    Permission(
        "music_options",
        "Musique — configuration",
        "Connexion du compte et réglages du service musical.",
        "Musique",
        RISK_MEDIUM,
    ),
    # --- Analyse ---
    Permission(
        "stats",
        "Statistiques",
        "Consultation des statistiques de fréquentation.",
        "Analyse",
        RISK_LOW,
    ),
]


# --- Index dérivés (calculés une fois à l'import) ---------------------------

#: Liste ordonnée des colonnes ``admin_<resource>`` du modèle Role.
PERMISSION_FIELDS = [p.field for p in PERMISSIONS]

#: Liste ordonnée des noms techniques de ressource (pour les gardes).
PERMISSION_RESOURCES = [p.resource for p in PERMISSIONS]

#: Accès direct par nom de ressource.
PERMISSIONS_BY_RESOURCE = {p.resource: p for p in PERMISSIONS}

#: Ensemble des ressources connues (pour vérifier une garde).
KNOWN_RESOURCES = frozenset(PERMISSION_RESOURCES)


def permissions_by_category():
    """Retourne un ``OrderedDict`` ``catégorie -> [Permission, ...]``.

    Les catégories conservent leur ordre de première apparition dans
    ``PERMISSIONS`` ; c'est l'ordre d'affichage du formulaire.
    """
    grouped = OrderedDict()
    for perm in PERMISSIONS:
        grouped.setdefault(perm.category, []).append(perm)
    return grouped
