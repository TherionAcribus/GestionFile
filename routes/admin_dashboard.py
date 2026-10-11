from flask import Blueprint, render_template, request, current_app as app
from flask_security import current_user
from models import DashboardCard, DashboardUserLayout, db
from communication import communikation
from routes.admin_security import check_default_admin, require_permission_api, user_has_permission
from dashboard_catalog import (CATALOG, apply_user_layout, card_info,
                               card_settings, default_layout, layout_from_order,
                               reorder_positions, validate_layout,
                               validate_settings, visible_cards)
from audit_service import record_audit
from audit_log import ACTION_DELETE, ACTION_UPDATE, OUTCOME_SUCCESS

admin_dashboard_bp = Blueprint('admin_dashboard', __name__)


@admin_dashboard_bp.app_context_processor
def _dashboard_helpers():
    """``dashboard_card_info(name)`` dans les gabarits des cartes (libellé,
    icône, page liée, largeur) — voir dashboard_catalog."""
    return {"dashboard_card_info": card_info}


def _can(resource):
    return user_has_permission(current_user, resource)


def _user_layout():
    """Ligne de disposition personnelle de l'admin courant (None sinon)."""
    if not getattr(current_user, 'is_authenticated', False):
        return None
    return (DashboardUserLayout.query
            .filter_by(user_id=current_user.id).first())


def _common_cards():
    return DashboardCard.query.order_by(DashboardCard.position, DashboardCard.id).all()


def _views_for_user():
    """Cartes vues par l'admin courant : sa disposition personnelle si elle
    existe, sinon la commune."""
    row = _user_layout()
    return apply_user_layout(
        _common_cards(), validate_layout(row.layout) if row else None)


def _save_user_layout(layout):
    """Crée/remplace la disposition personnelle de l'admin courant."""
    row = _user_layout()
    if row is None:
        row = DashboardUserLayout(user_id=current_user.id)
        db.session.add(row)
    row.layout = layout
    db.session.commit()
    record_audit(ACTION_UPDATE, "dashboard_user_layout",
                 target_id=current_user.id, outcome=OUTCOME_SUCCESS,
                 details=f"order={len(layout['order'])} hidden={len(layout['hidden'])}")


def _personalize_from_views(views):
    """Enregistre comme disposition personnelle la suite de vues (ordre +
    cartes masquées) passée en argument."""
    layout = layout_from_order(
        [view.name for view in views],
        [view.name for view in views if view.visible])
    _save_user_layout(layout)


def _reorder_views(ordered_ids):
    """Vues de l'admin courant réordonnées d'après les ids reçus (les cartes
    non citées gardent leur ordre relatif — reorder_positions)."""
    views = _views_for_user()
    positions = reorder_positions(views, ordered_ids)
    views.sort(key=lambda v: positions[v.id])
    return views


def _visible_cards_for_user():
    return visible_cards(_views_for_user(), _can)


def _ordered_ids(items):
    """Identifiants dans l'ordre demandé par le client.

    ``items`` : ``[{"id": ..., "position": ...}, ...]`` ; les entrées sont
    triées par ``position`` puis réduites à leur ``id`` (int). Lève
    ``ValueError`` sur toute charge mal formée.
    """
    if not isinstance(items, list):
        raise ValueError("order doit être une liste")
    try:
        return [int(item["id"]) for item in
                sorted(items, key=lambda item: int(item["position"]))]
    except (TypeError, KeyError, ValueError) as exc:
        raise ValueError("order mal formé") from exc


def _apply_order(ordered_ids):
    """Réordonne toutes les cartes (cf. reorder_positions) et renvoie-les."""
    cards = DashboardCard.query.all()
    positions = reorder_positions(cards, ordered_ids)
    for card in cards:
        card.position = positions[card.id]
    return cards


def _render_card_slots(cards):
    """Enveloppes à chargement différé des cartes (dashboard_slot.html)."""
    return "".join(render_template('admin/dashboard_slot.html', dashboardcard=card)
                   for card in cards if card.name in CATALOG)


def _user_slots_html():
    """Enveloppes des cartes visibles et autorisées pour l'admin courant."""
    return _render_card_slots(_visible_cards_for_user())

# La page d'accueil du tableau de bord n'exige que l'authentification (garantie
# par la garde globale ``/admin`` du point 1.2) : tout admin y accède et n'y voit
# que les cartes de son ressort. Les sous-routes qui *modifient* la configuration
# du tableau de bord exigent en revanche la permission 'options'.
@admin_dashboard_bp.route('/admin')
def admin():
    # Auto-afficher la carte sécurité si le mot de passe par défaut est encore en place
    security_card = DashboardCard.query.filter_by(name='security').first()
    if security_card and not security_card.visible and check_default_admin():
        security_card.visible = True
        security_card.position = 0
        db.session.commit()
        record_audit(ACTION_UPDATE, "dashboard_card", target_id="security",
                     outcome=OUTCOME_SUCCESS,
                     details="auto-affichage (admin par défaut actif)")

    # Seules les cartes que l'utilisateur peut charger (permission de la
    # route de la carte) : plus de carte « erreur » pour les autres.
    dashboardcards = [card for card in _visible_cards_for_user() if card.name in CATALOG]
    return render_template('/admin/admin.html',
                            dashboardcards=dashboardcards,
                            can_customize=_can('options'))

# Même garde que /admin : l'authentification globale suffit — chaque admin ne
# reçoit que les enveloppes des cartes de son ressort.
@admin_dashboard_bp.route('/admin/dashboard/cards')
def dashboard_cards():
    return _user_slots_html(), 200

# Masquer une carte : sans disposition personnelle ni permission 'options',
# l'admin ne masque que pour lui (disposition personnelle créée à la volée) ;
# avec disposition personnelle, idem ; sinon réglage commun (permission
# 'options' requise).
@admin_dashboard_bp.route('/admin/dashboard/hide', methods=['POST'])
def hide_dashboard_card():
    card_name = request.form.get('card_name')

    if _user_layout() is not None or not _can('options'):
        views = _views_for_user()
        if not any(view.name == card_name for view in views):
            return 'Card non trouvée', 404
        for view in views:
            if view.name == card_name:
                view.visible = False
        _personalize_from_views(views)
        return '', 200

    card = DashboardCard.query.filter_by(name=card_name).first()

    if card:
        card.visible = False
        db.session.commit()
        record_audit(ACTION_UPDATE, "dashboard_card", target_id=card_name,
                     outcome=OUTCOME_SUCCESS, details="visible=False")
        communikation("admin", event="refresh_dashboard_select")
        communikation("admin", event="refresh_dashboard")
        return '', 200
    else:
        return 'Card non trouvée', 404


# Tout admin peut gérer SA disposition (scope=me) ; l'étendue « all »
# (disposition commune) reste réservée à la permission 'options', vérifiée
# ici-même.
@admin_dashboard_bp.route('/admin/dashboard/display_select', methods=['GET'])
def dashboard_display_select():
    row = _user_layout()
    scope = request.args.get('scope')
    if scope is None:
        scope = 'me' if row else ('all' if _can('options') else 'me')
    if scope == 'all' and not _can('options'):
        return 'Permission refusée', 403
    cards = (apply_user_layout(_common_cards(), None)
             if scope == 'all' else _views_for_user())
    all_dashboardcards = [card for card in cards if card.name in CATALOG]
    return render_template('/admin/dashboard_select.html',
                        all_dashboardcards=all_dashboardcards,
                        scope=scope,
                        can_edit_common=_can('options'),
                        has_personal=bool(row),
                        card_info=card_info,
                        card_settings=card_settings,
                        can=_can)


# Disposition personnelle de l'admin courant : accessible à tout admin
# authentifié (garde globale /admin) — elle ne modifie que sa propre vue.
@admin_dashboard_bp.route('/admin/dashboard/me/save', methods=['POST'])
def save_my_dashboard():
    data = request.get_json(silent=True) or {}
    visible_cards = data.get('visible_cards', [])
    card_order = data.get('card_order', [])
    try:
        ordered_ids = _ordered_ids(card_order)
    except ValueError:
        return 'Invalid data', 400
    names_by_id = {card.id: card.name for card in DashboardCard.query.all()}
    names = [names_by_id[i] for i in ordered_ids if i in names_by_id]
    _save_user_layout(layout_from_order(names, visible_cards))
    return _user_slots_html(), 200


@admin_dashboard_bp.route('/admin/dashboard/me/reset', methods=['POST'])
def reset_my_dashboard():
    row = _user_layout()
    if row:
        db.session.delete(row)
        db.session.commit()
    record_audit(ACTION_DELETE, "dashboard_user_layout",
                 target_id=current_user.id, outcome=OUTCOME_SUCCESS,
                 details="retour à la disposition commune")
    return _user_slots_html(), 200


# Réordonner : avec disposition personnelle (ou sans permission 'options'),
# l'ordre n'est enregistré que pour l'admin courant ; sinon ordre commun.
@admin_dashboard_bp.route('/admin/dashboard/save_order', methods=['POST'])
def save_dashboard_order():
    data = request.get_json(silent=True) or {}  # Récupérer les données JSON envoyées depuis le frontend
    if 'order' not in data:
        return 'Invalid data', 400
    try:
        ordered_ids = _ordered_ids(data['order'])
    except ValueError:
        return 'Invalid data', 400
    if _user_layout() is not None or not _can('options'):
        _personalize_from_views(_reorder_views(ordered_ids))
        return '', 204
    _apply_order(ordered_ids)
    db.session.commit()  # Sauvegarder les modifications dans la base de données
    record_audit(ACTION_UPDATE, "dashboard_card", outcome=OUTCOME_SUCCESS,
                 details="réordonnancement")
    communikation("admin", event="refresh_dashboard")
    return '', 204  # Réponse vide avec succès


@admin_dashboard_bp.route('/admin/dashboard/reset', methods=['POST'])
@require_permission_api('options')
def reset_dashboard():
    layout = default_layout()
    for card in DashboardCard.query.all():
        if card.name in layout:
            card.visible, card.position = layout[card.name]
            card.settings = None
    db.session.commit()
    record_audit(ACTION_UPDATE, "dashboard_card", outcome=OUTCOME_SUCCESS,
                 details="réinitialisation (disposition d'usine)")
    communikation("admin", event="refresh_dashboard_select")
    communikation("admin", event="refresh_dashboard")
    return _user_slots_html(), 200


@admin_dashboard_bp.route('/admin/dashboard/settings/<name>', methods=['POST'])
@require_permission_api('options')
def save_dashboard_card_settings(name):
    if name not in CATALOG:
        return 'Card non trouvée', 404
    card = DashboardCard.query.filter_by(name=name).first()
    if not card:
        return 'Card non trouvée', 404
    try:
        clean = validate_settings(name, request.get_json(silent=True))
    except ValueError as exc:
        # Messages de validation rédigés dans validate_settings (sûrs à
        # renvoyer au client), jamais un détail d'exception technique.
        message = exc.args[0] if exc.args else 'Invalid data'
        return message, 400
    card.settings = {**(card.settings or {}), **clean}
    db.session.commit()
    record_audit(ACTION_UPDATE, "dashboard_card", target_id=name,
                 outcome=OUTCOME_SUCCESS, details=f"settings={clean}")
    communikation("admin", data={"name": name}, event="refresh_dashboard_card")
    return '', 204


@admin_dashboard_bp.route('/admin/dashboard/save_configuration', methods=['POST'])
@require_permission_api('options')
def save_dashboard_configuration():
    data = request.get_json(silent=True) or {}
    visible_cards = data.get('visible_cards', [])
    card_order = data.get('card_order', [])
    try:
        ordered_ids = _ordered_ids(card_order)
    except ValueError:
        return 'Invalid data', 400

    # Mettre à jour la visibilité
    all_cards = DashboardCard.query.all()
    for card in all_cards:
        card.visible = card.name in visible_cards

    # Mettre à jour l'ordre
    _apply_order(ordered_ids)

    db.session.commit()
    record_audit(ACTION_UPDATE, "dashboard_card", outcome=OUTCOME_SUCCESS,
                 details=f"config: visibles={','.join(map(str, visible_cards))}")
    communikation("admin", event="refresh_dashboard_select")
    communikation("admin", event="refresh_dashboard")
    
    # Enveloppes à chargement différé, comme au chargement de la page : chaque
    # carte appelle sa propre route (contenu et permission). Auparavant, cette
    # route reconstruisait à la main le contenu de chaque carte — logique
    # dupliquée, qui avait divergé de celle des routes.
    html = _user_slots_html()
    return html, 200