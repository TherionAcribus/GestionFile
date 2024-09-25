from flask import Blueprint, render_template, request, current_app as app
from models import Counter, Activity, db
from routes.admin_activity import display_activity_table

admin_counter_bp = Blueprint('admin_counter', __name__)

# page de base
@admin_counter_bp.route('/admin/counter')
def admin_counter():
    return render_template('/admin/counter.html',
                            counter_order = app.config['COUNTER_ORDER'])


# affiche le tableau des counters 
@admin_counter_bp.route('/admin/counter/table')
def display_counter_table():
    counters = Counter.query.all()
    activities = Activity.query.all()
    return render_template('admin/counter_htmx_table.html', counters=counters, activities = activities)


# mise à jour des informations d'un counter
@admin_counter_bp.route('/admin/counter/counter_update/<int:counter_id>', methods=['POST'])
def update_counter(counter_id):
    try:
        counter = Counter.query.get(counter_id)
        if counter:
            if request.form.get('name') == '':
                app.display_toast(success=False, message="Le nom est obligatoire")
                return ""
            counter.name = request.form.get('name', counter.name)
            activities_ids = request.form.getlist('activities')

            # Suppression des activités ajoutées pour éviter les erreur de duplication
            activities_ids = request.form.getlist('activities')
            new_activities = Activity.query.filter(Activity.id.in_(activities_ids)).all()

            # Clear existing activities and add the new ones
            counter.activities = new_activities

            db.session.commit()
            app.display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            app.display_toast(success=False, message="Comptoir introuvable")
            return ""

    except Exception as e:
            app.display_toast(success=False, message="erreur : " + str(e))
            app.logger.error(e)
            return ""


# affiche la modale pour confirmer la suppression d'un comptoir
@admin_counter_bp.route('/admin/counter/confirm_delete/<int:counter_id>', methods=['GET'])
def confirm_delete_counter(counter_id):
    counter = Counter.query.get(counter_id)
    print("counter", counter)
    return render_template('/admin/counter_modal_confirm_delete.html', counter=counter)


# supprime un comptoir
@admin_counter_bp.route('/admin/counter/delete/<int:counter_id>', methods=['GET'])
def delete_counter(counter_id):
    try:
        counter = Counter.query.get(counter_id)
        if not counter:
            app.display_toast(success=False, message="Comptoir introuvable")
            return display_counter_table()

        db.session.delete(counter)
        db.session.commit()

        app.display_toast(success=True, message="Comptoir supprimé")
        app.communikation("admin", event="refresh_counter_order")

        return display_counter_table()

    except Exception as e:
        app.display_toast(success=False, message="erreur : " + str(e))
        app.logger.error(e)
        return display_counter_table()


# affiche le formulaire pour ajouter un counter
@admin_counter_bp.route('/admin/counter/add_form')
def add_counter_form():
    activities = Activity.query.all()
    return render_template('/admin/counter_add_form.html', activities=activities)


# enregistre le comptoir dans la Bdd
@admin_counter_bp.route('/admin/counter/add_new_counter', methods=['POST'])
def add_new_counter():
    try:
        name = request.form.get('name')
        activities_ids = request.form.getlist('activities')

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Le nom est obligatoire")
            return display_activity_table()
        
        # Trouve l'ordre le plus élevé et ajoute 1, sinon commence à 0 si aucun bouton n'existe
        max_order_counter = Counter.query.order_by(Counter.sort_order.desc()).first()
        sort_order = max_order_counter.sort_order + 1 if max_order_counter else 0

        new_counter = Counter(
            name=name,
            sort_order=sort_order
        )
        db.session.add(new_counter)
        db.session.commit()


        # Associer les activités sélectionnées avec le nouveau pharmacien
        for activity_id in activities_ids:
            activity = Activity.query.get(int(activity_id))
            if activity:
                new_counter.activities.append(activity)
        db.session.commit()

        app.display_toast(success=True, message="Comptoir ajouté")
        
        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_counter_form"></div>"""

        return f"{display_counter_table()}{clear_form_html}"


    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message="erreur : " + str(e))
        app.logger.error(e)
        return display_activity_table()


@admin_counter_bp.route('/admin/counter/order_counter')
def order_counter_table():
    counters = Counter.query.order_by(Counter.sort_order).all()
    return render_template('admin/counter_order_counters.html', counters=counters)


@admin_counter_bp.route('/admin/counter/update_counter_order', methods=['POST'])
def update_counter_order():
    try:
        order_data = request.form.getlist('order[]')
        for index, counter_id in enumerate(order_data):
            print(counter_id, index)
            counter = Counter.query.order_by(Counter.sort_order).get(counter_id)
            print(counter)
            counter.sort_order = index
        db.session.commit()
        app.display_toast(success=True, message="Ordre mis à jour")
        return '', 200  # Réponse sans contenu
    except Exception as e:
        app.display_toast(success=False, message=f"Erreur: {e}")



