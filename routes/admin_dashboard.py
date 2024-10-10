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
    
    # Rechercher la card dans la base de données
    card = DashboardCard.query.filter_by(name=card_name).first()

    if card:
        # Modifier la visibilité de la card
        card.visible = False
        db.session.commit()
        communikation("admin", event="refresh_dashboard_select")
        return '', 200  # Réponse vide 
    else:
        return 'Card non trouvée', 404
    

@admin_dashboard_bp.route('/admin/dashboard/valide_select', methods=['POST'])
def dashboard_valid_select():
    data = request.form.getlist('dashboard_options')  # Récupérer les options envoyées

    # Récupérer toutes les cartes dans la base de données
    all_cards = DashboardCard.query.all()

    # Mettre à jour la visibilité de chaque carte
    for card in all_cards:
        # Si la carte est dans la sélection, elle est visible
        if card.name in data:
            card.visible = True
        else:
            # Sinon, elle est invisible
            card.visible = False

    db.session.commit()  # Appliquer les changements à la base de données

    return "", 200  # Retourner une réponse vide avec un code 200

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
            card_name = card_data['id']  # Maintenant on utilise 'id' comme le nom de la card
            position = int(card_data['position'])
            card = DashboardCard.query.filter_by(id=card_name).first()
            if card:
                card.position = position  # Mettre à jour la position
        db.session.commit()  # Sauvegarder les modifications dans la base de données
        return '', 204  # Réponse vide avec succès
    return 'Invalid data', 400
    