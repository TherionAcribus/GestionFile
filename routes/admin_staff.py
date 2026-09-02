from flask import Blueprint, render_template, request, jsonify, redirect, url_for, current_app as app
from models import Pharmacist, Activity, Counter, DashboardCard, db
from routes.admin_security import require_permission, require_permission_dashboard
from ui_feedback import display_toast

admin_staff_bp = Blueprint('admin_staff', __name__)

# base
@admin_staff_bp.route('/admin/staff')
@require_permission('staff')
def admin_staff():    
    return render_template('/admin/staff.html',
                            activities = Activity.query.all())

# affiche la table de l'équipe
@admin_staff_bp.route('/admin/staff/table')
@require_permission('staff')
def display_staff_table():
    staff = Pharmacist.query.all()
    activities = Activity.query.all()
    return render_template('admin/staff_htmx_table.html', staff=staff, activities=activities)

# mise à jour des informations d'un membre
@admin_staff_bp.route('/admin/staff/member_update/<int:member_id>', methods=['POST'])
@require_permission('staff')
def update_member(member_id):
    try:
        member = Pharmacist.query.get(member_id)
        if member:
            if request.form.get('name') == '':
                display_toast(success=False, message="Le nom est obligatoire")
                return "", 204
            if request.form.get('initials') == '':
                display_toast(success=False, message="Les initiales sont obligatoires")
                return "", 204

            # Vérifie que les initiales ne sont pas déjà enregistrées par une autre personne
            initials = request.form.get("initials")
            existing_member = db.session.query(Pharmacist).filter(
                Pharmacist.initials == initials,
                Pharmacist.id != member_id  # Exclure le membre actuel
            ).first()

            if existing_member:
                display_toast(success=False, message="Les initiales sont déjà utilisées par un autre membre")
                return "", 204

            member.name = request.form.get('name', member.name)
            member.initials = initials
            member.language = request.form.get('language', member.language)

            # Suppression des activités ajoutées pour éviter les erreurs de duplication
            activities_ids = request.form.getlist('activities')
            new_activities = Activity.query.filter(Activity.id.in_(activities_ids)).all()

            # Clear existing activities and add the new ones
            member.activities = new_activities

            db.session.commit()
            display_toast(success=True, message="Mise à jour réussie")
            return ""
        else:
            display_toast(success=False, message="Membre de l'équipe introuvable")
            return ""

    except Exception as e:
        display_toast(success=False, message="Erreur : " + str(e))
        return jsonify(status="error", message=str(e)), 500


# affiche la modale pour confirmer la suppression d'un membre
@admin_staff_bp.route('/admin/staff/confirm_delete/<int:member_id>', methods=['GET'])
@require_permission('staff')
def confirm_delete(member_id):
    staff = Pharmacist.query.get(member_id)
    return render_template('/admin/staff_modal_confirm_delete.html', staff=staff)


# supprime un membre de l'equipe
@admin_staff_bp.route('/admin/staff/delete/<int:member_id>', methods=['DELETE'])
@require_permission('staff')
def delete_staff(member_id):
    try:
        member = Pharmacist.query.get(member_id)
        if not member:
            display_toast(success=False, message="Membre de l'équipe non trouvé")
            return display_staff_table()

        db.session.delete(member)
        db.session.commit()
        display_toast(success=True, message="Suppression réussie")
        return display_staff_table()

    except Exception as e:
        display_toast(success=False, message="Erreur : " + str(e))
        return display_staff_table()
    

# affiche le formulaire pour ajouter un membre
@admin_staff_bp.route('/admin/staff/add_form')
@require_permission('staff')
def add_staff_form():
    activities = Activity.query.all()
    return render_template('/admin/staff_add_form.html', activities=activities)


# enregistre le membre dans la Bdd
@admin_staff_bp.route('/admin/staff/add_new_staff', methods=['POST'])
@require_permission('staff')
def add_new_staff():
    try:
        name = request.form.get('name')
        initials = request.form.get('initials')
        language = request.form.get('language')
        activities_ids = request.form.getlist('activities')

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Nom obligatoire")
            return display_staff_table()
        if not initials:  # Vérifiez que les champs obligatoires sont remplis
            display_toast(success=False, message="Initiales obligatoires")
            return display_staff_table()
        if initials in [initial[0] for initial in db.session.query(Pharmacist.initials).all()]:
            display_toast(success=False, message="Les initiales sont déjà utilisées")
            return "", 204

        new_staff = Pharmacist(
            name=name,
            initials=initials,
            language=language,
        )
        db.session.add(new_staff)
        db.session.commit()

        # Associer les activités sélectionnées avec le nouveau pharmacien
        for activity_id in activities_ids:
            activity = Activity.query.get(int(activity_id))
            if activity:
                new_staff.activities.append(activity)
        db.session.commit()

        display_toast(success=True, message="Membre ajouté avec succès")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_staff_form"></div>"""

        return f"{display_staff_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message= "Erreur : " + str(e))
        return display_staff_table()
    

@admin_staff_bp.route('/admin/staff/dashboard')
@require_permission_dashboard('staff')
def dashboard_staff():
    app.logger.debug("dashboard staff")
    staffs = Pharmacist.query.all()
    dashboardcard = DashboardCard.query.filter_by(name="staff").first()
    return render_template('/admin/dashboard_staff.html', 
                            staffs=staffs,
                            dashboardcard=dashboardcard)



# ---------------------------------------------------------------------------
# Routes historiques, sans prefixe /admin, deplacees depuis app.py (point 9.5d).
# Elles precedent la refonte de l'administration ; conservees telles quelles.
# ---------------------------------------------------------------------------

@admin_staff_bp.route('/add_counter', methods=['POST'])
@require_permission('counter')
def add_counter():
    if request.method == 'POST':
        name = request.form['name']
        new_counter = Counter(name=name)
        db.session.add(new_counter)
        db.session.commit()
        return redirect('/admin')
    return "Erreur dans la soumission du formulaire"



@admin_staff_bp.route('/pharmacists')
@require_permission('staff')
def pharmacists():
    all_pharmacists = Pharmacist.query.all()
    app.logger.debug('ALL %s', all_pharmacists)
    return render_template('pharmacists.html', pharmacists=all_pharmacists)



@admin_staff_bp.route('/update_pharmacist/<int:pharmacist_id>', methods=['POST'])
@require_permission('staff')
def update_pharmacist(pharmacist_id):
    pharmacist = Pharmacist.query.get(pharmacist_id)
    if pharmacist:
        pharmacist.name = request.form.get('name', pharmacist.name)
        pharmacist.initials = request.form.get('initials', pharmacist.initials)
        pharmacist.language = request.form.get('language', pharmacist.language)
        pharmacist.is_active = 'is_active' in request.form
        pharmacist.activity = request.form.get('activity', pharmacist.activity)
        db.session.commit()
    return redirect(url_for('admin_staff.pharmacists'))



@admin_staff_bp.route('/add_pharmacist', methods=['POST'])
@require_permission('staff')
def add_pharmacist():
    name = request.form.get('name')
    initials = request.form.get('initials')
    language = request.form.get('language')
    is_active = request.form.get('is_active') == 'on'
    activity = request.form.get('activity')
    new_pharmacist = Pharmacist(name=name, initials=initials, language=language, is_active=is_active, activity=activity)
    db.session.add(new_pharmacist)
    db.session.commit()
    return render_template('htmx/menu_admin_pharmacist_row.html', pharmacist=new_pharmacist)



@admin_staff_bp.route('/new_pharmacist_form')
@require_permission('staff')
def new_pharmacist_form():
    app.logger.debug("new_pharmacist_form")
    return render_template('htmx/menu_admin_new_pharmacist_form.html')


