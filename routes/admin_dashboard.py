from flask import Blueprint,render_template, request, current_app as app
from sqlalchemy.orm import joinedload
from models import DashboardCard, db, Pharmacist, Patient, Counter, Button
from communication import communikation
from routes.admin_security import check_default_admin, require_permission, require_permission_api

admin_dashboard_bp = Blueprint('admin_dashboard', __name__)

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

    dashboardcards = DashboardCard.query.filter_by(visible=True).order_by(DashboardCard.position).all()
    return render_template('/admin/admin.html',
                            dashboardcards=dashboardcards)

@admin_dashboard_bp.route('/admin/dashboard/hide', methods=['POST'])
@require_permission('options')
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
    communikation("admin", event="refresh_dashboard_select")

    dashboardcards = DashboardCard.query.filter_by(visible=True).order_by(DashboardCard.position).all()
    html = ""
    for dashboardcard in dashboardcards:
        template_name = f'admin/dashboard_load_{dashboardcard.name}.html'
        html += render_template(template_name, dashboardcard=dashboardcard)
    return html, 200

@admin_dashboard_bp.route('/admin/dashboard/display_select', methods=['GET'])
@require_permission('options')
def dashboard_display_select():
    all_dashboardcards = DashboardCard.query.all()
    return render_template('/admin/dashboard_select.html',
                        all_dashboardcards=all_dashboardcards)


@admin_dashboard_bp.route('/admin/dashboard/save_order', methods=['POST'])
@require_permission_api('options')
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
        return '', 204
    else:
        return 'Card non trouvée', 404

@admin_dashboard_bp.route('/admin/dashboard/add', methods=['POST'])
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
    
    return '', 201

@admin_dashboard_bp.route('/admin/dashboard/save_configuration', methods=['POST'])
@require_permission_api('options')
def save_dashboard_configuration():
    data = request.get_json()
    visible_cards = data.get('visible_cards', [])
    card_order = data.get('card_order', [])
    
    # Mettre à jour la visibilité
    all_cards = DashboardCard.query.all()
    for card in all_cards:
        card.visible = card.name in visible_cards
    
    # Mettre à jour l'ordre
    for card_data in card_order:
        card_id = int(card_data['id'])
        position = int(card_data['position'])
        card = DashboardCard.query.filter_by(id=card_id).first()
        if card:
            card.position = position
    
    db.session.commit()
    communikation("admin", event="refresh_dashboard_select")
    
    # Retourner le HTML des cartes visibles avec leur contenu
    dashboardcards = DashboardCard.query.filter_by(visible=True).order_by(DashboardCard.position).all()
    html = ""
    
    for dashboardcard in dashboardcards:
        # Préparer les données nécessaires pour chaque type de carte
        context = {'dashboardcard': dashboardcard}
        
        if dashboardcard.name == 'staff':
            context['staffs'] = Pharmacist.query.all()
            
        elif dashboardcard.name == 'queue':
            # dashboard_queue.html lit patient.activity.name par ligne → joinedload.
            context['patients'] = Patient.query.options(joinedload(Patient.activity)).all()
            
        elif dashboardcard.name == 'button':
            # Logique pour les boutons (copié depuis admin_patient.py)
            all_buttons = Button.query.all()
            # Index id -> bouton : évite une requête par groupe pour retrouver le
            # parent (déjà chargé dans all_buttons) — cf. admin_patient.dashboard_button.
            buttons_by_id = {button.id: button for button in all_buttons}
            grouped_buttons = {}
            other_buttons = []

            for button in all_buttons:
                if button.parent_button_id:
                    parent_id = button.parent_button_id
                    if parent_id not in grouped_buttons:
                        parent_button = buttons_by_id.get(parent_id)
                        grouped_buttons[parent_id] = {
                            'parent': parent_button,
                            'children': []
                        }
                    grouped_buttons[parent_id]['children'].append(button)
                elif button.is_parent:
                    if button.id not in grouped_buttons:
                        grouped_buttons[button.id] = {
                            'parent': button,
                            'children': []
                        }
                else:
                    other_buttons.append(button)
            
            sorted_groups = sorted(grouped_buttons.values(), key=lambda x: x['parent'].label.lower())
            for group in sorted_groups:
                group['children'].sort(key=lambda x: x.label.lower())
            
            other_buttons.sort(key=lambda x: x.label.lower())
            
            context['grouped_buttons'] = sorted_groups
            context['other_buttons'] = other_buttons
            
        elif dashboardcard.name == 'counter':
            # dashboard_counter.html lit counter.staff.name par ligne → joinedload.
            context['counters'] = Counter.query.options(joinedload(Counter.staff)).all()
            
        elif dashboardcard.name == 'connection':
            context['namespaces'] = list(app.active_connections.keys())
            
        elif dashboardcard.name == 'appschedule':
            # Mêmes infos que la route /admin/appschedule/dashboard, via le même
            # assembleur : une seule requête pour toutes les dernières exécutions
            # (au lieu d'une par tâche) — cf. scheduler_dashboard, point 5.3.
            from scheduler import scheduler
            from scheduler_dashboard import build_jobs_info

            context['main_jobs'], context['other_jobs'] = build_jobs_info(scheduler.get_jobs())
        
        elif dashboardcard.name == 'security':
            context['is_default_admin'] = check_default_admin()

        elif dashboardcard.name == 'alerts':
            # Mêmes alertes que la route /admin/alerts/dashboard.
            from routes.admin_patient import get_patient_page_alerts
            context['alerts'] = get_patient_page_alerts()

        # Utiliser le template avec contenu, pas le wrapper
        template_name = f'admin/dashboard_{dashboardcard.name}.html'
        try:
            html += render_template(template_name, **context)
        except Exception as e:
            print(f"Erreur lors du rendu de {template_name}: {e}")
            # Fallback sur le wrapper si le template n'existe pas
            html += render_template(f'admin/dashboard_load_{dashboardcard.name}.html', dashboardcard=dashboardcard)
    
    return html, 200