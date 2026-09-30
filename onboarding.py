"""Catalogue et transitions du parcours de configuration guidée (« onboarding »).

Module **pur** : aucune dépendance Flask ni base de données. Il décrit les
étapes du parcours (identifiants stables, permission requise, destinations
internes fixes vers les pages existantes) et les transitions d'état. La
persistance vit dans ``models.AdminOnboardingState`` et l'accès HTTP dans
``routes/admin_onboarding.py``.

Principes :

- le guide n'écrit jamais dans ``ConfigOption`` et ne déclenche aucune action
  métier (aucun patient créé, aucun ticket imprimé, aucune annonce jouée,
  aucun écran rechargé) ;
- une étape « passée » ne compte **pas** comme vérifiée ; seule une
  confirmation explicite (« Marquer comme vérifié ») la fait passer en
  ``verified`` ;
- les permissions sont injectées via un prédicat ``can(resource) -> bool``
  (typiquement ``user_has_permission``), ce qui garde le module testable et
  indépendant de la session.
"""

from __future__ import annotations

from datetime import datetime, timezone

# --- Statuts du parcours ------------------------------------------------------
STATUS_PENDING = "pending"        # jamais commencé / invitation affichable
STATUS_ACTIVE = "active"          # parcours en cours
STATUS_PAUSED = "paused"          # mis en pause, reprenable
STATUS_DISMISSED = "dismissed"    # invitation déclinée : ne plus solliciter
STATUS_COMPLETED = "completed"    # terminé explicitement (« Terminer »)

STATUSES = frozenset({
    STATUS_PENDING, STATUS_ACTIVE, STATUS_PAUSED, STATUS_DISMISSED,
    STATUS_COMPLETED,
})

# --- Statuts d'étape ----------------------------------------------------------
STEP_TODO = "todo"          # défaut : jamais confirmée
STEP_VERIFIED = "verified"  # « vérifié par vous » (confirmation explicite)
STEP_SKIPPED = "skipped"    # passée volontairement — ne compte pas comme vérifiée

STEP_STATUSES = frozenset({STEP_TODO, STEP_VERIFIED, STEP_SKIPPED})

# Identifiant de l'étape bilan, affichée dans la page du guide (pas de page
# métier associée).
SUMMARY_STEP_ID = "summary"

# --- Actions de l'endpoint (liste fermée) -------------------------------------
ACTION_START = "start"
ACTION_DISMISS = "dismiss"
ACTION_PAUSE = "pause"
ACTION_RESUME = "resume"
ACTION_SELECT = "select"
ACTION_VERIFY = "verify"
ACTION_SKIP = "skip"
ACTION_FINISH = "finish"

ACTIONS = frozenset({
    ACTION_START, ACTION_DISMISS, ACTION_PAUSE, ACTION_RESUME,
    ACTION_SELECT, ACTION_VERIFY, ACTION_SKIP, ACTION_FINISH,
})

# Codes d'erreur stables renvoyés par apply_action (traduits en messages par la
# couche route).
ERROR_UNKNOWN_ACTION = "action_inconnue"
ERROR_UNKNOWN_STEP = "etape_inconnue"
ERROR_STEP_FORBIDDEN = "etape_interdite"
ERROR_INVALID_STATE = "etat_invalide"


# --- Catalogue ----------------------------------------------------------------
# Chaque étape : identifiant stable, permission requise (None = accessible à
# tout compte authentifié), titre, objectif, points de vigilance et liens fixes
# vers les pages existantes. Aucune URL n'est fournie par le client : la
# destination est toujours calculée ici.
CATALOG = (
    {
        "id": "security",
        "permission": "security",
        "title": "Sécurité et comptes",
        "description": "Vérifiez les comptes d'administration et le mot de "
                       "passe initial avant toute autre chose.",
        "url": "/admin/security",
        "links": ({"label": "Ouvrir Sécurité", "url": "/admin/security"},),
        "points": (
            "Changer le mot de passe initial si le compte par défaut est encore actif.",
            "Vérifier que chaque compte a le bon rôle ; aucun secret n'est affiché par le guide.",
        ),
    },
    {
        "id": "pharmacy",
        "permission": "app",
        "title": "Identité de la pharmacie",
        "description": "Nom affiché et règle de numérotation des tickets.",
        "url": "/admin/app/general",
        "links": ({"label": "Ouvrir Application", "url": "/admin/app/general"},),
        "points": (
            "Attention : changer la numérotation en journée peut faire recommencer la série.",
            "Si la borne patient tourne sur ce serveur, renseignez l'adresse "
            "réseau (bouton « Détecter ») pour que les QR codes fonctionnent "
            "sur les téléphones.",
        ),
    },
    {
        "id": "activities",
        "permission": "activity",
        "title": "Activités",
        "description": "Services proposés aux patients, lettres d'appel et "
                       "disponibilité. Les horaires sont facultatifs.",
        "url": "/admin/activity?tab=activity",
        "links": ({"label": "Ouvrir Activités", "url": "/admin/activity?tab=activity"},),
        "points": (
            "Les boutons de la borne patient seront reliés à ces activités.",
        ),
    },
    {
        "id": "staff",
        "permission": "staff",
        "title": "Équipe",
        "description": "Collaborateurs, initiales et compétences.",
        "url": "/admin/staff",
        "links": ({"label": "Ouvrir Équipe", "url": "/admin/staff"},),
        "points": (
            "Ce sont les compétences du collaborateur connecté qui déterminent "
            "les patients qu'un comptoir peut servir.",
        ),
    },
    {
        "id": "counters",
        "permission": "counter",
        "title": "Comptoirs",
        "description": "Postes de travail et lien avec le collaborateur connecté.",
        "url": "/admin/counter",
        "links": ({"label": "Ouvrir Comptoirs", "url": "/admin/counter"},),
        "points": (
            "Un comptoir sert les patients selon les compétences de la personne "
            "qui s'y connecte, pas selon d'anciennes activités du poste.",
        ),
    },
    {
        "id": "patient",
        "permission": "patient",
        "title": "Borne patient",
        "description": "Boutons reliés aux activités, puis apparence, tickets "
                       "et QR codes selon les besoins.",
        "url": "/admin/patient/buttons",
        "links": (
            {"label": "Boutons", "url": "/admin/patient/buttons"},
            {"label": "Apparence", "url": "/admin/patient/visual"},
            {"label": "Tickets", "url": "/admin/patient/ticket"},
            {"label": "QR codes", "url": "/admin/patient/qrcode"},
        ),
        "points": (
            "Options d'impression et de suivi par téléphone selon les besoins.",
        ),
    },
    {
        "id": "announce",
        "permission": "announce",
        "title": "Affichage et annonces",
        "description": "Écran d'annonce visuel et voix/sons de l'appel.",
        "url": "/admin/announce/visual",
        "links": (
            {"label": "Apparence", "url": "/admin/announce/visual"},
            {"label": "Audio", "url": "/admin/announce/audio"},
        ),
        "points": (
            "Trois gestes distincts dans l'éditeur : enregistrer le brouillon, "
            "publier, appliquer aux écrans. Le guide ne les déclenche jamais.",
        ),
    },
    {
        "id": SUMMARY_STEP_ID,
        "permission": None,  # étape bilan affichée dans la page du guide
        "title": "Bilan",
        "description": "Revoyez ce qui a été vérifié par vous, passé ou laissé "
                       "à faire, et contrôlez manuellement les appareils.",
        "url": None,
        "links": (),
        "points": (
            "Le guide ne crée ni patient, ni ticket, ni annonce : les contrôles "
            "de la borne, de l'imprimante et des écrans restent manuels.",
        ),
    },
)

_CATALOG_BY_ID = {step["id"]: step for step in CATALOG}
_STEP_ORDER = [step["id"] for step in CATALOG]

# Correspondance pages → étape : préfixes des pages admin couvertes par le
# catalogue. Seules les pages complètes (qui étendent base.html) affichent
# l'encart ; les fragments HTMX n'héritent pas de la base et ne l'appellent pas.
_PAGE_STEP_PREFIXES = (
    ("/admin/security", "security"),
    ("/admin/app", "pharmacy"),
    ("/admin/activity", "activities"),
    ("/admin/staff", "staff"),
    ("/admin/counter", "counters"),
    ("/admin/patient", "patient"),
    ("/admin/announce", "announce"),
)


def catalog():
    """Le catalogue complet, dans l'ordre de parcours."""
    return list(CATALOG)


def step_by_id(step_id):
    """L'étape du catalogue d'identifiant ``step_id``, ou ``None``."""
    if step_id is None:
        return None
    return _CATALOG_BY_ID.get(step_id)


def is_step_permitted(step, can):
    """``True`` si l'étape est accessible selon le prédicat ``can(resource)``."""
    if step is None:
        return False
    permission = step.get("permission")
    return permission is None or bool(can(permission))


def available_steps(can):
    """Étapes accessibles à l'utilisateur courant (permission satisfaite)."""
    return [step for step in CATALOG if is_step_permitted(step, can)]


def step_status(steps_state, step_id):
    """Statut d'une étape dans ``steps_state`` — ``todo`` si absente/inconnue."""
    entry = (steps_state or {}).get(step_id)
    if isinstance(entry, dict) and entry.get("status") in STEP_STATUSES:
        return entry["status"]
    return STEP_TODO


def set_step_status(steps_state, step_id, status):
    """Renvoie une NOUVELLE carte d'états avec ``step_id`` à ``status``.

    Ne mute pas le dict d'entrée (JSON SQLAlchemy : la réassignation de la
    colonne garantit la détection du changement).
    """
    if status not in STEP_STATUSES:
        raise ValueError(f"statut d'étape invalide: {status!r}")
    new_state = dict(steps_state or {})
    new_state[step_id] = {
        "status": status,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return new_state


def step_for_path(path):
    """Identifiant d'étape associé à une page admin, ou ``None``.

    La correspondance est par préfixe exact (``/admin/app`` ≠ ``/admin/apps``) :
    toutes les sous-pages d'une section relèvent de la même étape.
    """
    if not path:
        return None
    for prefix, step_id in _PAGE_STEP_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return step_id
    return None


def first_available_step_id(can):
    """Première étape de parcours accessible (hors bilan), sinon ``summary``."""
    for step in CATALOG:
        if step["id"] == SUMMARY_STEP_ID:
            continue
        if is_step_permitted(step, can):
            return step["id"]
    return SUMMARY_STEP_ID


def next_step_id(after_id, steps_state, can):
    """Prochaine étape « à faire » après ``after_id`` (circulaire), sinon le bilan.

    Scan du catalogue à partir de la position qui suit ``after_id``, en bouclant
    au début : la première étape accessible et encore ``todo`` gagne. Quand tout
    est résolu (vérifié ou passé), le parcours pointe sur l'étape bilan.
    """
    ids = [sid for sid in _STEP_ORDER if sid != SUMMARY_STEP_ID]
    try:
        start = ids.index(after_id) + 1
    except ValueError:
        start = 0
    ordered = ids[start:] + ids[:start]
    for step_id in ordered:
        step = step_by_id(step_id)
        if is_step_permitted(step, can) and step_status(steps_state, step_id) == STEP_TODO:
            return step_id
    return SUMMARY_STEP_ID


def progress(steps_state, can):
    """Compteurs pédagogiques du parcours (étape bilan exclue des totaux).

    - ``total`` : étapes accessibles à l'utilisateur ;
    - ``verified`` / ``skipped`` / ``todo`` : répartition de ces étapes ;
    - ``unavailable`` : étapes hors des permissions de l'utilisateur.
    """
    result = {"total": 0, "verified": 0, "skipped": 0, "todo": 0, "unavailable": 0}
    for step in CATALOG:
        if step["id"] == SUMMARY_STEP_ID:
            continue
        if not is_step_permitted(step, can):
            result["unavailable"] += 1
            continue
        result["total"] += 1
        result[step_status(steps_state, step["id"])] += 1
    return result


def steps_view(steps_state, can, current_step=None):
    """Vue gabarit du catalogue : une entrée par étape, permissions résolues."""
    view = []
    for step in CATALOG:
        permitted = is_step_permitted(step, can)
        view.append({
            "id": step["id"],
            "title": step["title"],
            "description": step["description"],
            "url": step.get("url"),
            "links": tuple(step.get("links") or ()),
            "points": tuple(step.get("points") or ()),
            "permission": step.get("permission"),
            "permitted": permitted,
            "status": step_status(steps_state, step["id"]),
            "is_current": current_step == step["id"],
            "is_summary": step["id"] == SUMMARY_STEP_ID,
        })
    return view


def _empty_state():
    """État minimal quand aucune ligne n'existe encore (rien n'est persisté)."""
    return {"status": STATUS_PENDING, "current_step": None, "steps": {}}


def apply_action(state, action, step_id, can):
    """Applique une action à un état-dict, renvoie ``(nouvel_état, erreur)``.

    ``state`` est ``{"status":…, "current_step":…, "steps":{…}}`` ou ``None``
    (parcours jamais commencé — traité comme ``pending``). En cas d'erreur,
    renvoie ``(None, ERROR_*)`` ; les codes sont en liste fermée.

    Transitions :
    - ``start`` : (re)démarre depuis n'importe quel statut ; conserve les
      étapes déjà marquées (revoir une étape ne réinitialise rien) ;
    - ``dismiss`` : mémorise le refus — l'invitation n'est plus affichée ;
    - ``pause``/``resume`` : active ↔ paused (resume aussi depuis pending ou
      dismissed — relance toujours possible) ;
    - ``select`` : désigne l'étape courante (doit exister et être permise) ;
    - ``verify``/``skip`` : marquent l'étape ; si c'était l'étape courante, le
      curseur avance à la prochaine étape « à faire » (ou au bilan) ;
    - ``finish`` : clôt le parcours (depuis active ou paused).

    Aucune transition n'est possible hors parcours actif pour les actions
    d'étape : mettre à jour l'état pédagogique exige un parcours démarré.
    """
    if action not in ACTIONS:
        return None, ERROR_UNKNOWN_ACTION

    snapshot = dict(state) if state else _empty_state()
    snapshot["steps"] = dict(snapshot.get("steps") or {})
    status = snapshot.get("status") or STATUS_PENDING
    current = snapshot.get("current_step")

    if action == ACTION_START:
        snapshot["status"] = STATUS_ACTIVE
        step = step_by_id(current)
        if step is None or not is_step_permitted(step, can):
            snapshot["current_step"] = first_available_step_id(can)
        return snapshot, None

    if action == ACTION_DISMISS:
        snapshot["status"] = STATUS_DISMISSED
        return snapshot, None

    if action == ACTION_PAUSE:
        if status != STATUS_ACTIVE:
            return None, ERROR_INVALID_STATE
        snapshot["status"] = STATUS_PAUSED
        return snapshot, None

    if action == ACTION_RESUME:
        # Idempotent : reprendre un parcours déjà actif (bouton « Reprendre »
        # de l'accueil) est accepté comme un no-op.
        if status not in (STATUS_ACTIVE, STATUS_PAUSED, STATUS_DISMISSED, STATUS_PENDING):
            return None, ERROR_INVALID_STATE
        snapshot["status"] = STATUS_ACTIVE
        step = step_by_id(current)
        if step is None or not is_step_permitted(step, can):
            snapshot["current_step"] = first_available_step_id(can)
        return snapshot, None

    if action == ACTION_FINISH:
        if status not in (STATUS_ACTIVE, STATUS_PAUSED):
            return None, ERROR_INVALID_STATE
        snapshot["status"] = STATUS_COMPLETED
        return snapshot, None

    # Actions d'étape (select / verify / skip) : parcours actif requis.
    if status != STATUS_ACTIVE:
        return None, ERROR_INVALID_STATE
    step = step_by_id(step_id)
    if step is None:
        return None, ERROR_UNKNOWN_STEP
    if not is_step_permitted(step, can):
        return None, ERROR_STEP_FORBIDDEN

    if action == ACTION_SELECT:
        snapshot["current_step"] = step_id
        return snapshot, None

    # verify / skip : le bilan n'est pas une étape « vérifiable ».
    if step_id == SUMMARY_STEP_ID:
        return None, ERROR_UNKNOWN_STEP
    new_status = STEP_VERIFIED if action == ACTION_VERIFY else STEP_SKIPPED
    snapshot["steps"] = set_step_status(snapshot["steps"], step_id, new_status)
    if not current or current == step_id:
        snapshot["current_step"] = next_step_id(step_id, snapshot["steps"], can)
    return snapshot, None
