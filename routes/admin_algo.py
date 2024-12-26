from flask import Blueprint, render_template, request, jsonify, current_app as app
from datetime import datetime
from models import AlgoRule, Activity, ConfigOption, db
from routes.admin_security import require_permission

admin_algo_bp = Blueprint('admin_algo', __name__)

# page de base
@admin_algo_bp.route('/admin/algo')
@require_permission('algo')
def admin_algo():
    algo_overtaken_limit = app.config['ALGO_OVERTAKEN_LIMIT']
    return render_template('/admin/algo.html',
                            algo_overtaken_limit=algo_overtaken_limit)

@admin_algo_bp.route('/admin/algo/table')
def display_algo_table():
    rules = AlgoRule.query.all()
    activities = Activity.query.all()
    return render_template('admin/algo_htmx_table.html', rules=rules, activities=activities)

# affiche le formulaire activer ou desactiver l'algorithme
@admin_algo_bp.route('/admin/button_des_activate_algo')
def button_des_activate_algo():
    return render_template("admin/algo_des_activate_buttons.html",
                            algo_activated= app.config['ALGO_IS_ACTIVATED'])

# active ou desactive l'algorithme, enregistre l'info, retourne les boutons
@admin_algo_bp.route('/admin/algo/toggle_activation')
def toggle_activation():
    action = request.args.get('action', 'activate')
    is_activated = action == 'activate'
    
    app.config['ALGO_IS_ACTIVATED'] = is_activated
    algo_activated = ConfigOption.query.filter_by(config_key="algo_activate").first()
    algo_activated.value_bool = is_activated
    db.session.commit()

    return render_template("admin/algo_des_activate_buttons.html",
                            algo_activated=app.config['ALGO_IS_ACTIVATED'])


@admin_algo_bp.route('/admin/algo/change_overtaken_limit', methods=['POST'])
def change_overtaken_limit():
    overtaken_limit = request.form.get('overtaken_limit')

    app.config['ALGO_OVERTAKEN_LIMIT'] = overtaken_limit
    try:
        algo_overtaken_limit = ConfigOption.query.filter_by(config_key="algo_overtaken_limit").first()
        algo_overtaken_limit.value_int = overtaken_limit
        db.session.commit()
        return app.display_toast()
    except Exception as e:
        print(e)
        return app.display_toast(success=False, message=str(e))


# affiche le formulaire pour ajouter une regle de l'algo
@admin_algo_bp.route('/admin/algo/add_rule_form')
def add_rule_form():
    activities = Activity.query.all()
    return render_template('/admin/algo_add_rule_form.html', activities=activities)


# enregistre la regledans la Bdd
@admin_algo_bp.route('/admin/algo/add_new_rule', methods=['POST'])
def add_new_rule():
    try:
        name = request.form.get('name')
        activity = Activity.query.get(request.form.get('activity_id'))
        priority_level = request.form.get('priority_level')
        min_patients = request.form.get('min_patients')
        max_patients = request.form.get('max_patients')
        max_overtaken = request.form.get('max_overtaken')
        start_time_str = request.form.get('start_time')            
        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time_str = request.form.get('end_time')
        end_time = datetime.strptime(end_time_str, "%H:%M").time()

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Nom obligatoire")
            return display_algo_table()

        new_rule = AlgoRule(
            name=name,
            activity = activity,
            priority_level = priority_level,
            min_patients = min_patients,
            max_patients = max_patients,
            max_overtaken = max_overtaken,
            start_time = start_time,
            end_time = end_time
        )
        db.session.add(new_rule)
        db.session.commit()

        app.display_toast(success=True, message="Règle ajoutée avec succès")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_rule_form"></div>"""

        return f"{display_algo_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message="erreur : " + str(e))
        print(e)
        return display_algo_table()


# affiche la modale pour confirmer la suppression d'un membre
@admin_algo_bp.route('/admin/algo/confirm_delete_rule/<int:rule_id>', methods=['GET'])
def confirm_delete_rule(rule_id):
    rule = AlgoRule.query.get(rule_id)
    return render_template('/admin/algo_modal_confirm_delete_rule.html', rule=rule)


# supprime une regle de l'algo
@admin_algo_bp.route('/admin/algo/delete_rule/<int:algo_id>', methods=['GET'])
def delete_algo(algo_id):
    try:
        rule = AlgoRule.query.get(algo_id)
        if not rule:
            app.display_toast(success=False, message="Règle non trouvée")
            return display_algo_table()

        db.session.delete(rule)
        db.session.commit()

        app.display_toast(success=True, message="Règle supprimée")
        return display_algo_table()

    except Exception as e:
        app.display_toast(success=False, message="erreur : " + str(e))
        return display_algo_table()


@admin_algo_bp.route('/admin/algo/rule_update/<int:rule_id>', methods=['POST'])
def update_algo_rule(rule_id):
    try:
        rule = AlgoRule.query.get(rule_id)
        if rule:
            if request.form.get('name') == '':
                app.display_toast(success=False, message="Le nom est obligatoire")
                return ""
            elif request.form.get('min_patients') > request.form.get('max_patients'):
                app.display_toast(success=False, message="Le nombre de patients maximum doit être superieur au nombre de patients minimum")
                return ""

            rule.name = request.form.get('name', rule.name)
            activity = Activity.query.get(request.form.get('activity_id', rule.activity_id))
            rule.activity = activity
            rule.priority_level = request.form.get('priority_level', rule.priority_level)
            rule.min_patients = request.form.get('min_patients', rule.min_patients)
            rule.max_patients = request.form.get('max_patients', rule.max_patients)
            rule.max_overtaken = request.form.get('max_overtaken', rule.max_overtaken)
            start_time_str = request.form.get('start_time', rule.start_time.strftime("%H:%M"))            
            rule.start_time = datetime.strptime(start_time_str, "%H:%M").time()
            end_time_str = request.form.get('end_time', rule.end_time.strftime("%H:%M"))
            rule.end_time = datetime.strptime(end_time_str, "%H:%M").time()

            db.session.commit()

            app.display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            app.display_toast(success=False, message="Règle introuvable")
            return ""

    except Exception as e:
            app.display_toast(success=False, message="erreur : " + str(e))
            app.logger.error(e)
            return jsonify(status="error", message=str(e)), 500