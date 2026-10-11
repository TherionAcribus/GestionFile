from flask import Blueprint, render_template, request, current_app as app
from flask_security import current_user
from models import DashboardCard, db
from communication import communikation
from routes.admin_security import check_default_admin, require_permission, require_permission_api, user_has_permission
from dashboard_catalog import (CATALOG, card_info, card_settings,
                               reorder_positions, validate_settings, visible_cards)
from audit_service import record_audit
from audit_log import ACTION_UPDATE, OUTCOME_SUCCESS

admin_dashboard_bp = Blueprint('admin_dashboard', __name__)


@admin_dashboard_bp.app_context_processor
def _dashboard_helpers():
    """``dashboard_card_info(name)`` dans les gabarits des cartes (libellé,
    icône, page liée, largeur) — voir dashboard_catalog."""
    return {"dashboard_card_info": card_info}


def _can(resource):
    return user_has_permission(current_user, resource)


def _visible_cards_for_user():
    cards = DashboardCard.query.order_by(DashboardCard.position, DashboardCard.id).all()
    return visible_cards(cards, _can)


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

@admin_dashboard_bp.route('/admin/dashboard/hide', methods=['POST'])
@require_permission('options')
def hide_dashboard_card():
    card_name = request.form.get('card_name')
    
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


@admin_dashboard_bp.route('/admin/dashboard/display_select', methods=['GET'])
@require_permission('options')
def dashboard_display_select():
    all_dashboardcards = [card for card in DashboardCard.query.order_by(DashboardCard.position, DashboardCard.id).all()
                          if card.name in CATALOG]
    return render_template('/admin/dashboard_select.html',
                        all_dashboardcards=all_dashboardcards,
                        card_info=card_info,
                        card_settings=card_settings,
                        can=_can)


@admin_dashboard_bp.route('/admin/dashboard/save_order', methods=['POST'])
@require_permission_api('options')
def save_dashboard_order():
    data = request.get_json(silent=True) or {}  # Récupérer les données JSON envoyées depuis le frontend
    if 'order' not in data:
        return 'Invalid data', 400
    try:
        ordered_ids = _ordered_ids(data['order'])
    except ValueError:
        return 'Invalid data', 400
    _apply_order(ordered_ids)
    db.session.commit()  # Sauvegarder les modifications dans la base de données
    record_audit(ACTION_UPDATE, "dashboard_card", outcome=OUTCOME_SUCCESS,
                 details="réordonnancement")
    communikation("admin", event="refresh_dashboard")
    return '', 204  # Réponse vide avec succès


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