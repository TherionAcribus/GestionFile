"""Parcours de configuration guidée de l'administration.

Le catalogue des étapes et les transitions (fonctions pures) vivent dans
``onboarding.py`` ; la persistance par compte dans ``models.AdminOnboardingState``.

Garde-fous :

- l'utilisateur cible est toujours ``current_user`` (session), jamais un
  identifiant transmis par le navigateur ;
- actions et étapes sont en liste fermée (``onboarding.ACTIONS`` et le
  catalogue) ; les destinations de redirection sont calculées ici, pas fournies
  par le client ;
- les GET ne créent ni ne modifient aucun état ;
- le guide n'écrit jamais dans ``ConfigOption`` et ne déclenche aucune action
  métier (patient, impression, annonce, rechargement d'écran) ;
- CSRF : couvert par la garde navigateur globale ``csrf_protect_browser_requests``
  (app.py) — les formulaires portent ``csrf_token`` et csrf.js injecte
  ``X-CSRFToken`` sur les appels HTMX ;
- verrouillage optimiste : une écriture basée sur un ``version`` périmé est
  refusée 409 plutôt que d'écraser la progression faite dans un autre onglet.
"""

import re
from datetime import datetime

from flask import (
    Blueprint, current_app, jsonify, redirect, render_template, request, url_for,
)
from flask_security import current_user
from sqlalchemy.exc import IntegrityError

import onboarding as ob
from audit_log import ACTION_UPDATE, OUTCOME_SUCCESS
from audit_service import record_audit
from config import time_tz
from models import (
    Activity, AdminOnboardingState, Button, Counter, PageEditorState,
    Pharmacist, User, db,
)
from routes.admin_security import user_has_permission
from ui_feedback import display_toast

admin_onboarding_bp = Blueprint('admin_onboarding', __name__)

_ORIGINS = frozenset({"panel", "checklist", "home"})

_ERROR_MESSAGES = {
    ob.ERROR_UNKNOWN_ACTION: "Action inconnue.",
    ob.ERROR_UNKNOWN_STEP: "Étape inconnue.",
    ob.ERROR_STEP_FORBIDDEN: "Cette étape n'est pas accessible avec vos droits.",
    ob.ERROR_INVALID_STATE: "Cette action n'est pas possible dans l'état actuel du parcours.",
}

_TOAST_MESSAGES = {
    ob.ACTION_START: "Parcours de configuration démarré.",
    ob.ACTION_DISMISS: "Parcours masqué. Le lien « Configuration guidée » permet de le reprendre.",
    ob.ACTION_PAUSE: "Parcours mis en pause.",
    ob.ACTION_RESUME: "Parcours repris.",
    ob.ACTION_SELECT: "Étape courante définie.",
    ob.ACTION_VERIFY: "Étape marquée comme vérifiée.",
    ob.ACTION_SKIP: "Étape passée.",
    ob.ACTION_FINISH: "Parcours terminé.",
}


def _can(resource):
    """Prédicat de permission de l'utilisateur courant (injecté dans le noyau pur)."""
    return user_has_permission(current_user, resource)


def _state_for(user):
    """Ligne de parcours de ``user`` — ``None`` tant que rien n'a été démarré."""
    return AdminOnboardingState.query.filter_by(user_id=user.id).first()


# ---------------------------------------------------------------------------
# Contexte gabarit (comme _dashboard_helpers dans routes/admin_dashboard.py)
# ---------------------------------------------------------------------------

@admin_onboarding_bp.app_context_processor
def _onboarding_helpers():
    """Expose ``onboarding_panel_context()`` et ``onboarding_home_context()``
    à tous les gabarits — base.html / admin.html gardent ``is defined`` pour
    les fixtures où ce blueprint n'est pas enregistré."""
    return {
        "onboarding_panel_context": onboarding_panel_context,
        "onboarding_home_context": onboarding_home_context,
    }


# Marqueurs repérant les comptes d'exemple/test laissés dans l'équipe.
# Comparaison par mot (token) : « Attestation » ne doit pas matcher « test ».
_EXAMPLE_MARKERS = ("exemple", "test", "demo", "démo", "bidule")


def _tokens(text):
    """Mots d'un nom (minuscules, sans ponctuation) pour le repérage d'exemples."""
    return re.findall(r"[a-zà-ÿ0-9]+", (text or "").lower())


def _names(members, limit=6):
    """Liste courte de noms pour les signalements (tronquée au-delà du seuil).

    Les éléments listés exposent ``name`` (Pharmacist, Activity) ou ``label``
    (Button).
    """
    names = ", ".join(getattr(m, "name", None) or m.label
                      for m in members[:limit])
    if len(members) > limit:
        names += "…"
    return names


def _notices_security():
    """Étape Sécurité : comptes encore dotés d'un mot de passe temporaire."""
    count = User.query.filter_by(must_change_password=True).count()
    if count:
        return [
            f"{count} compte(s) ont encore un mot de passe temporaire à "
            "changer — faites-le avant la mise en service."
        ]
    return []


def _notices_pharmacy():
    """Étape Pharmacie : nom et adresse réseau utilisés côté patient
    (tickets, écrans, QR codes de suivi)."""
    notices = []
    if not current_app.config.get("PHARMACY_NAME"):
        notices.append(
            "Le nom de la pharmacie n'est pas renseigné — il apparaît sur "
            "les tickets et les écrans."
        )
    if not current_app.config.get("NETWORK_ADRESS"):
        notices.append(
            "L'adresse réseau n'est pas renseignée : si la borne patient "
            "tourne sur ce serveur, les QR codes de suivi ne fonctionneront "
            "pas sur les téléphones."
        )
    return notices


def _notices_activities():
    """Étape Activités : catalogue vide et activités sans collaborateur
    compétent (personne ne pourra appeler leurs patients)."""
    notices = []
    if Activity.query.count() == 0:
        notices.append(
            "Aucune activité définie : la borne patient n'aura rien à "
            "proposer."
        )
        return notices
    without_staff = (Activity.query.outerjoin(Activity.pharmacists)
                     .filter(Pharmacist.id.is_(None))
                     .order_by(Activity.name).all())
    if without_staff:
        notices.append(
            f"{len(without_staff)} activité(s) sans collaborateur compétent "
            f"({_names(without_staff)}) : personne ne pourra appeler leurs "
            "patients."
        )
    return notices


def _notices_staff():
    """Étape Équipe : les comptes ressemblant à des exemples (restes d'une
    installation de test) et les membres sans compétences — ils pourront se
    connecter à un comptoir mais n'appelleront aucun patient."""
    notices = []
    members = Pharmacist.query.order_by(Pharmacist.name).all()
    examples = [m for m in members
                if any(token.startswith(_EXAMPLE_MARKERS)
                       for token in _tokens(m.name))]
    if examples:
        notices.append(
            f"{len(examples)} membre(s) ressemblant à des exemples : "
            f"{_names(examples)}. Supprimez-les si ce sont des restes de test."
        )
    without_skills = [m for m in members if not m.activities]
    if without_skills:
        notices.append(
            f"{len(without_skills)} membre(s) sans compétences : "
            f"{_names(without_skills)}. Ils ne pourront appeler aucun "
            "patient — cochez leurs activités si ces comptes sont réels."
        )
    return notices


def _notices_counters():
    """Étape Comptoirs : sans comptoir configuré, aucun appel possible."""
    if Counter.query.count() == 0:
        return [
            "Aucun comptoir configuré : personne ne pourra appeler de "
            "patient."
        ]
    return []


def _notices_patient():
    """Étape Borne patient : boutons affichés sans effet (ni activité ni
    sous-menu — ils ne créeront aucun ticket) et activités non reliées à un
    bouton de la borne, donc inaccessibles aux patients."""
    notices = []
    orphans = (Button.query
               .filter(Button.is_present.is_(True),
                       Button.is_active.is_(True),
                       Button.activity_id.is_(None),
                       db.or_(Button.is_parent.is_(False),
                              Button.is_parent.is_(None)))
               .order_by(Button.label).all())
    if orphans:
        notices.append(
            f"{len(orphans)} bouton(s) affiché(s) sans activité ni sous-menu "
            f"({_names(orphans)}) : ils ne créeront aucun ticket."
        )
    unlinked = (Activity.query.outerjoin(Activity.buttons)
                .filter(Button.id.is_(None))
                .order_by(Activity.name).all())
    if unlinked:
        notices.append(
            f"{len(unlinked)} activité(s) ne sont reliées à aucun bouton de "
            f"la borne ({_names(unlinked)}) : inaccessibles aux patients."
        )
    return notices


def _notices_announce():
    """Étape Affichage et annonces : un brouillon enregistré dans l'éditeur
    mais jamais publié n'est pas encore pris en compte par les écrans."""
    draft = (PageEditorState.query
             .filter_by(page_key="announce")
             .filter(PageEditorState.draft_json.isnot(None))
             .first())
    if draft is not None:
        return [
            "Un brouillon de l'écran d'annonces n'est pas publié — les "
            "écrans n'en tiennent pas encore compte."
        ]
    return []


_STEP_NOTICES = {
    "security": _notices_security,
    "pharmacy": _notices_pharmacy,
    "activities": _notices_activities,
    "staff": _notices_staff,
    "counters": _notices_counters,
    "patient": _notices_patient,
    "announce": _notices_announce,
    # « summary » : pas de signalement.
}


def _step_notices(step_id):
    """Signalements factuels en lecture seule, répartis par étape.

    Chaque étape délègue à sa fonction ``_notices_*`` ; les étapes sans
    signalement (``summary``) renvoient une liste vide.
    Jamais d'exception : un défaut d'une notice d'aide ne doit pas casser
    la page hôte.
    """
    try:
        collect = _STEP_NOTICES.get(step_id)
        return collect() if collect is not None else []
    except Exception:  # pragma: no cover - signalement d'aide, jamais bloquant
        current_app.logger.exception("_step_notices")
        return []


def _panel_dict(step_id, state):
    """Dict d'encart pour une étape précise — ou ``None`` si rien à montrer.

    ``None`` hors parcours (non démarré / terminé / décliné), quand l'étape
    n'existe pas dans le catalogue ou qu'elle est hors des droits de
    l'utilisateur.
    """
    if state is None or state.status not in (ob.STATUS_ACTIVE, ob.STATUS_PAUSED):
        return None
    step = ob.step_by_id(step_id)
    if step is None or not ob.is_step_permitted(step, _can):
        return None
    steps_state = state.steps_state or {}
    return {
        "status": state.status,
        "step": step,
        "step_status": ob.step_status(steps_state, step["id"]),
        "is_current": state.current_step == step["id"],
        "version": state.version,
        "progress": ob.progress(steps_state, _can),
        "notices": _step_notices(step["id"]),
    }


def onboarding_panel_context():
    """Contexte de l'encart contextuel à afficher sur la page courante, ou None.

    ``None`` hors parcours (non démarré / terminé / décliné), pour un visiteur
    anonyme ou sur une page sans étape associée. Ne lève jamais : un défaut de
    l'encart d'aide ne doit pas casser la page admin hôte.
    """
    try:
        if not getattr(current_user, "is_authenticated", False):
            return None
        step_id = ob.step_for_path(request.path)
        if step_id is None:
            return None
        return _panel_dict(step_id, _state_for(current_user))
    except Exception:  # pragma: no cover - encart d'aide, jamais bloquant
        current_app.logger.exception("onboarding_panel_context")
        return None


def onboarding_home_context():
    """Contexte de l'encart de l'accueil admin : invitation (``pending``) ou
    carte de reprise (``active``/``paused``) ; ``None`` si anonyme ou après
    refus/clôture. Jamais d'exception."""
    try:
        if not getattr(current_user, "is_authenticated", False):
            return None
        state = _state_for(current_user)
        steps_state = (state.steps_state or {}) if state else {}
        step = ob.step_by_id(state.current_step) if state and state.current_step else None
        return {
            "status": state.status if state else ob.STATUS_PENDING,
            "version": state.version if state else 0,
            "progress": ob.progress(steps_state, _can),
            "step": step,
            "resume_url": (step or {}).get("url")
                          or url_for("admin_onboarding.onboarding_page"),
        }
    except Exception:  # pragma: no cover - encart d'aide, jamais bloquant
        current_app.logger.exception("onboarding_home_context")
        return None


def _page_context(state):
    """Variables du gabarit de la page/checklist pour l'utilisateur courant."""
    steps_state = (state.steps_state or {}) if state else {}
    current = state.current_step if state else None
    steps = ob.steps_view(steps_state, _can, current_step=current)
    for s in steps:
        # Notices affichées dans la checklist : calculées uniquement pour les
        # étapes permises et non bilan — ≤6 requêtes légères par rendu de
        # checklist, coût acceptable.
        if s["permitted"] and not s["is_summary"]:
            s["notices"] = _step_notices(s["id"])
        else:
            s["notices"] = []
        # Horodatage du dernier marquage (ISO UTC en base) converti dans le
        # fuseau du projet ; une valeur illisible est simplement ignorée.
        at_label = None
        if s["at"]:
            try:
                at_label = (datetime.fromisoformat(s["at"])
                            .astimezone(time_tz)
                            .strftime("%d/%m à %H:%M"))
            except (TypeError, ValueError):
                at_label = None
        s["at_label"] = at_label
        s["title_with_at"] = s["title"] + (f" ({at_label})" if at_label else "")
    return {
        "steps": steps,
        "progress": ob.progress(steps_state, _can),
        "state_status": state.status if state else ob.STATUS_PENDING,
        "current_step": current,
        "version": state.version if state else 0,
        "verified_steps": [s for s in steps
                           if s["permitted"] and s["status"] == ob.STEP_VERIFIED],
        "skipped_steps": [s for s in steps
                          if s["permitted"] and s["status"] == ob.STEP_SKIPPED],
        "todo_steps": [s for s in steps if s["permitted"] and not s["is_summary"]
                       and s["status"] == ob.STEP_TODO],
        "unavailable_steps": [s for s in steps if not s["permitted"]],
        "show_summary": current == ob.SUMMARY_STEP_ID
                        or (state is not None and state.status == ob.STATUS_COMPLETED),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@admin_onboarding_bp.route('/admin/onboarding')
def onboarding_page():
    """Page du guide : checklist des étapes et bilan.

    Authentification seulement (tout compte admin : chaque étape garde sa
    propre permission à l'ouverture de la page métier). Jamais de mutation en
    GET : l'état n'est créé que par une action explicite.
    """
    if not getattr(current_user, "is_authenticated", False):
        return redirect(url_for('security.login'))
    return render_template('admin/onboarding.html', **_page_context(_state_for(current_user)))


def _error(status, message):
    """Réponse d'erreur uniforme (JSON ; pas de diffusion aux autres admins :
    l'acquittement — toast inclus — part par l'en-tête HX-Trigger au seul
    auteur de la requête)."""
    display_toast(success=False, message=message, broadcast=False)
    return jsonify({"error": message}), status


def _step_url(state):
    """URL interne de l'étape courante de ``state`` — page du guide en repli."""
    step = ob.step_by_id(state.current_step) if state and state.current_step else None
    return (step or {}).get("url") or url_for("admin_onboarding.onboarding_page")


def _redirect_for(action, state, origin):
    """Destination serveur après l'action, ou ``None`` (reste sur place, fragment)."""
    if action == ob.ACTION_START:
        # Depuis l'accueil ou la checklist : ouvrir la page du guide.
        return url_for("admin_onboarding.onboarding_page")
    if action == ob.ACTION_RESUME and origin != "panel":
        return _step_url(state)
    if action in (ob.ACTION_VERIFY, ob.ACTION_SKIP, ob.ACTION_SELECT) and origin == "panel":
        # « Marquer vérifié et continuer » : l'étape courante a avancé (ou est
        # restée la prochaine chose à faire) — le navigateur suit.
        return _step_url(state)
    if action == ob.ACTION_FINISH:
        return url_for("admin_onboarding.onboarding_page")
    return None


def _fragment_for(origin, state, step_id):
    """Fragment de la zone d'origine re-rendu avec l'état (frais) donné.

    ``state`` est la ligne ORM à jour ; ``step_id`` sert uniquement au panneau
    (étape postée, repli étape courante) — ``_panel_dict`` renvoie None pour
    une étape inconnue/interdite, le fragment est alors vide.
    """
    if origin == "checklist":
        return render_template('admin/_onboarding_checklist.html', **_page_context(state))
    if origin == "home":
        return render_template('admin/_onboarding_home.html',
                               onboarding_home=onboarding_home_context())
    # Fragment « panneau » : la requête porte sur /admin/onboarding/action,
    # l'étape affichée vient du step_id posté (repli : étape courante) —
    # jamais de request.path, jamais d'étape arbitraire non validée.
    panel = _panel_dict(step_id or (state.current_step if state else None), state)
    return render_template('admin/_onboarding_panel.html', onboarding_panel=panel)


def _respond(state, action, origin, step_id, next_url):
    """Forme de la réponse : HTMX (fragment ou HX-Redirect), JSON ou navigation."""
    if request.headers.get("HX-Request"):
        if next_url:
            response = current_app.response_class("")
            response.headers["HX-Redirect"] = next_url
            return response
        return _fragment_for(origin, state, step_id)
    if request.is_json:
        return jsonify({
            "ok": True,
            "status": state.status,
            "current_step": state.current_step,
            "version": state.version,
            "next_url": next_url,
        })
    return redirect(next_url or url_for("admin_onboarding.onboarding_page"), code=303)


@admin_onboarding_bp.route('/admin/onboarding/action', methods=['POST'])
def onboarding_action():
    """Actions explicites du guide : start, dismiss, pause, resume, select,
    verify, skip, finish (liste fermée — ``onboarding.ACTIONS``).

    Corps JSON ou formulaire ; ``version`` active le verrouillage optimiste
    quand il est fourni. Toute la validation (action, étape, permission,
    statut du parcours) est faite par ``onboarding.apply_action``.
    """
    if not getattr(current_user, "is_authenticated", False):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or request.form
    action = (data.get('action') or '').strip()
    step_id = data.get('step_id') or None
    origin = data.get('origin') or 'panel'
    if origin not in _ORIGINS:
        origin = 'panel'

    if action not in ob.ACTIONS:
        return _error(400, _ERROR_MESSAGES[ob.ERROR_UNKNOWN_ACTION])

    version = None
    raw_version = data.get('version')
    if raw_version not in (None, ''):
        try:
            version = int(raw_version)
        except (TypeError, ValueError):
            return _error(400, "Version invalide.")

    state = _state_for(current_user)
    # Conflit de versions : deux onglets ont écrit — refus propre plutôt qu'une
    # progression perdue silencieusement.
    if state is not None and version is not None and version != state.version:
        if request.headers.get("HX-Request"):
            # HTMX ne swappe pas les 4xx : plutôt qu'un 409 (toast « Réessayer »
            # qui reposterait la même version périmée en boucle), on re-rend le
            # fragment d'origine avec l'état à jour — statut 200, formulaires
            # synchronisés sur la nouvelle version, action rejouable.
            display_toast(success=False, broadcast=False,
                          message="Une autre fenêtre a modifié le parcours — "
                                  "l'affichage a été actualisé. Réessayez si besoin.")
            return _fragment_for(origin, state, step_id)
        return jsonify({"error": "Conflit de version", "version": state.version}), 409

    snapshot = {
        "status": state.status if state else ob.STATUS_PENDING,
        "current_step": state.current_step if state else None,
        "steps": dict(state.steps_state) if state and state.steps_state else {},
    }
    new_state, error = ob.apply_action(snapshot, action, step_id, _can)
    if error:
        return _error(400, _ERROR_MESSAGES[error])

    if state is None:
        state = AdminOnboardingState(user_id=current_user.id, version=0)
        db.session.add(state)
    state.status = new_state["status"]
    state.current_step = new_state["current_step"]
    state.steps_state = new_state["steps"]
    state.version = (state.version or 0) + 1

    try:
        db.session.commit()
    except IntegrityError:
        # Deux démarrages simultanés sur un compte sans ligne : la contrainte
        # unique user_id tranche — le perdant reçoit un conflit à rejouer.
        db.session.rollback()
        if request.headers.get("HX-Request"):
            # Même choix que le conflit de version : on synchronise la vue du
            # perdant sur la ligne du gagnant (fragment 200) plutôt qu'un 409
            # que HTMX ne swapperait pas.
            display_toast(success=False, broadcast=False,
                          message="Deux démarrages simultanés — le parcours "
                                  "a été synchronisé.")
            return _fragment_for(origin, _state_for(current_user), step_id)
        return jsonify({"error": "Conflit d'écriture", "version": None}), 409

    record_audit(ACTION_UPDATE, "admin_onboarding", target_id=state.id,
                 outcome=OUTCOME_SUCCESS,
                 details=f"action={action} step={step_id or '-'} status={state.status}")

    # Le parcours est strictement par utilisateur : pas de toast chez les
    # autres administrateurs (broadcast=False -> retour HX-Trigger seul).
    display_toast(success=True, message=_TOAST_MESSAGES[action],
                  broadcast=False)
    return _respond(state, action, origin, step_id,
                    _redirect_for(action, state, origin))
