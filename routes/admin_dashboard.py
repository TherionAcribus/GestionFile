from flask import Blueprint, render_template, request, current_app as app
from flask_security import current_user
from models import DashboardCard, db
from communication import communikation
from routes.admin_security import check_default_admin, require_permission, require_permission_api, user_has_permission
from dashboard_catalog import CATALOG, card_info, missing_cards, reorder_positions, visible_cards
from audit_service import record_audit
from audit_log import ACTION_CREATE, ACTION_UPDATE, OUTCOME_SUCCESS

admin_dashboard_bp = Blueprint('admin_dashboard', __name__)


@admin_dashboard_bp.app_context_processor
def _dashboard_helpers():
    """``dashboard_card_info(name)`` dans les gabarits des cartes (libellé,
    icône, page liée, largeur) — voir dashboard_catalog."""
    return {"dashboard_card_info": card_info}


def _can(resource):
    return user_has_permission(current_user, resource)


def _ensure_catalog_cards():
    """Crée les cartes du catalogue absentes de la base (nouvelle carte après
    une mise à jour : « Aujourd'hui »…). Les cartes affichées par défaut sont
    placées en tête."""
    names = [name for (name,) in db.session.query(DashboardCard.name).all()]
    missing = missing_cards(names)
    if not missing:
        return
    first = (db.session.query(db.func.min(DashboardCard.position)).scalar() or 0) - len(missing)
    last = db.session.query(db.func.max(DashboardCard.position)).scalar() or 0
    for index, name in enumerate(missing, start=1):
        info = card_info(name)
        db.session.add(DashboardCard(
            name=name, visible=info.default_visible,
            position=(first + index) if info.default_visible else (last + index),
            size='36', color='bg-white'))
    db.session.commit()
    record_audit(ACTION_CREATE, "dashboard_card", outcome=OUTCOME_SUCCESS,
                 details=f"ajout automatique : {','.join(missing)}")


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

    _ensure_catalog_cards()
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
    

# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @admin_dashboard_bp.route('/admin/dashboard/valide_select', methods=['POST'])
@require_permission('options')
def dashboard_valid_select():
    data = request.form.getlist('dashboard_options')

    all_cards = DashboardCard.query.all()

    for card in all_cards:
        if card.name in data:
            card.visible = True
        else:
            card.visible = False

    db.session.commit()
    record_audit(ACTION_UPDATE, "dashboard_card", outcome=OUTCOME_SUCCESS,
                 details=f"visibles={','.join(data)}")
    communikation("admin", event="refresh_dashboard_select")

    dashboardcards = DashboardCard.query.filter_by(visible=True).order_by(DashboardCard.position, DashboardCard.id).all()
    html = ""
    for dashboardcard in dashboardcards:
        html += render_template('admin/dashboard_slot.html', dashboardcard=dashboardcard)
    return html, 200

@admin_dashboard_bp.route('/admin/dashboard/display_select', methods=['GET'])
@require_permission('options')
def dashboard_display_select():
    all_dashboardcards = [card for card in DashboardCard.query.order_by(DashboardCard.position, DashboardCard.id).all()
                          if card.name in CATALOG]
    return render_template('/admin/dashboard_select.html',
                        all_dashboardcards=all_dashboardcards,
                        card_info=card_info,
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

# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @admin_dashboard_bp.route('/admin/dashboard/resize', methods=['POST'])
@require_permission_api('options')
def resize_dashboard_card():
    data = request.get_json()
    card_id = data.get('card_id')
    new_size = data.get('size')
    
    if not card_id or not new_size:
        return 'Missing card_id or size', 400
    
    if new_size not in ['18', '24', '36', '48']:
        return 'Invalid size', 400
    
    card = DashboardCard.query.filter_by(id=card_id).first()
    if card:
        card.size = new_size
        db.session.commit()
        record_audit(ACTION_UPDATE, "dashboard_card", target_id=card_id,
                     outcome=OUTCOME_SUCCESS, details=f"size={new_size}")
        return '', 204
    else:
        return 'Card non trouvée', 404

# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @admin_dashboard_bp.route('/admin/dashboard/add', methods=['POST'])
@require_permission_api('options')
def add_dashboard_card():
    data = request.get_json()
    name = data.get('name')
    
    if not name:
        return 'Missing name', 400
    
    # Vérifier si la carte existe déjà
    existing_card = DashboardCard.query.filter_by(name=name).first()
    if existing_card:
        return 'Card already exists', 409
    
    # Trouver la position maximale actuelle
    max_position = db.session.query(db.func.max(DashboardCard.position)).scalar() or 0
    
    # Créer la nouvelle carte
    new_card = DashboardCard(
        name=name,
        visible=True,
        position=max_position + 1,
        size='36',
        color='bg-white'
    )
    
    db.session.add(new_card)
    db.session.commit()
    record_audit(ACTION_CREATE, "dashboard_card", target_id=new_card.id,
                 outcome=OUTCOME_SUCCESS, details=f"name={name}")
    
    return '', 201

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