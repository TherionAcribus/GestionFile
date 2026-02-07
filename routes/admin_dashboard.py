from flask import Blueprint,render_template, request, current_app as app
from models import DashboardCard, db
from communication import communikation

admin_dashboard_bp = Blueprint('admin_dashboard', __name__)

@admin_dashboard_bp.route('/admin')
def admin():
    dashboardcards = DashboardCard.query.filter_by(visible=True).order_by(DashboardCard.position).all()
    return render_template('/admin/admin.html',
                            dashboardcards=dashboardcards)

@admin_dashboard_bp.route('/admin/dashboard/hide', methods=['POST'])
def hide_dashboard_card():
    card_name = request.form.get('card_name')
    
    card = DashboardCard.query.filter_by(name=card_name).first()

    if card:
        card.visible = False
        db.session.commit()
        communikation("admin", event="refresh_dashboard_select")
        return '', 200
    else:
        return 'Card non trouvée', 404
    

@admin_dashboard_bp.route('/admin/dashboard/valide_select', methods=['POST'])
def dashboard_valid_select():
    data = request.form.getlist('dashboard_options')

    all_cards = DashboardCard.query.all()

    for card in all_cards:
        if card.name in data:
            card.visible = True
        else:
            card.visible = False

    db.session.commit()
    communikation("admin", event="refresh_dashboard_select")

    dashboardcards = DashboardCard.query.filter_by(visible=True).order_by(DashboardCard.position).all()
    html = ""
    for dashboardcard in dashboardcards:
        template_name = f'admin/dashboard_load_{dashboardcard.name}.html'
        html += render_template(template_name, dashboardcard=dashboardcard)
    return html, 200

@admin_dashboard_bp.route('/admin/dashboard/display_select', methods=['GET'])
def dashboard_display_select():
    all_dashboardcards = DashboardCard.query.all()
    return render_template('/admin/dashboard_select.html',
                        all_dashboardcards=all_dashboardcards)


@admin_dashboard_bp.route('/admin/dashboard/save_order', methods=['POST'])
def save_dashboard_order():
    data = request.get_json()  # Récupérer les données JSON envoyées depuis le frontend
    if 'order' in data:
        for card_data in data['order']:
            card_id = int(card_data['id'])
            position = int(card_data['position'])
            card = DashboardCard.query.filter_by(id=card_id).first()
            if card:
                card.position = position  # Mettre à jour la position
        db.session.commit()  # Sauvegarder les modifications dans la base de données
        return '', 204  # Réponse vide avec succès
    return 'Invalid data', 400

@admin_dashboard_bp.route('/admin/dashboard/resize', methods=['POST'])
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
        return '', 204
    else:
        return 'Card non trouvée', 404

@admin_dashboard_bp.route('/admin/dashboard/add', methods=['POST'])
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
    
    return '', 201