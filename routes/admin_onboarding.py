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

from flask import (
    Blueprint, current_app, jsonify, redirect, render_template, request, url_for,
)
from flask_security import current_user
from sqlalchemy.exc import IntegrityError

import onboarding as ob
from audit_log import ACTION_UPDATE, OUTCOME_SUCCESS
from audit_service import record_audit
from models import AdminOnboardingState, Pharmacist, db
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
    """Liste courte de noms pour les signalements (tronquée au-delà du seuil)."""
    names = ", ".join(m.name for m in members[:limit])
    if len(members) > limit:
        names += "…"
    return names


def _step_notices(step_id):
    """Signalements factuels par étape (lecture seule, jamais d'exception).

    Sur l'étape Équipe : les comptes ressemblant à des exemples (restes d'une
    installation de test) et les membres sans compétences — ils pourront se
    connecter à un comptoir mais n'appelleront aucun patient.
    """
    try:
        if step_id == "staff":
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
    """Réponse d'erreur uniforme (JSON ; le toast part quand même via l'event admin)."""
    display_toast(success=False, message=message)
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


def _respond(state, action, origin, step_id, next_url):
    """Forme de la réponse : HTMX (fragment ou HX-Redirect), JSON ou navigation."""
    if request.headers.get("HX-Request"):
        if next_url:
            response = current_app.response_class("")
            response.headers["HX-Redirect"] = next_url
            return response
        if origin == "checklist":
            return render_template('admin/_onboarding_checklist.html', **_page_context(state))
        if origin == "home":
            return render_template('admin/_onboarding_home.html',
                                   onboarding_home=onboarding_home_context())
        # Fragment « panneau » : la requête porte sur /admin/onboarding/action,
        # l'étape affichée vient du step_id posté (repli : étape courante) —
        # jamais de request.path, jamais d'étape arbitraire non validée.
        panel = _panel_dict(step_id or state.current_step, state)
        return render_template('admin/_onboarding_panel.html', onboarding_panel=panel)
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
        return jsonify({"error": "Conflit d'écriture", "version": None}), 409

    record_audit(ACTION_UPDATE, "admin_onboarding", target_id=state.id,
                 outcome=OUTCOME_SUCCESS,
                 details=f"action={action} step={step_id or '-'} status={state.status}")

    display_toast(success=True, message=_TOAST_MESSAGES[action])
    return _respond(state, action, origin, step_id,
                    _redirect_for(action, state, origin))
