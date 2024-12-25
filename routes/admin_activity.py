from datetime import datetime, time
from flask import Blueprint, render_template, request, url_for, redirect, send_from_directory, current_app as app
from models import Activity, ActivitySchedule, Pharmacist, Button, DashboardCard, db
from sqlalchemy.orm import joinedload
from routes.admin_security import require_permission

admin_activity_bp = Blueprint('admin_activity', __name__)

# page de base
@admin_activity_bp.route('/admin/activity')
@require_permission('activity')
def admin_activity():
    valid_tabs = ['activity', 'schedule']
    tab = request.args.get('tab', 'activity')
    if tab not in valid_tabs:
        tab = 'activity'

    return render_template('/admin/activity.html')

# affiche le tableau des activités 
@admin_activity_bp.route('/admin/activity/table')
def display_activity_table():
    activities = Activity.query.filter_by(is_staff=False).all()
    schedules = ActivitySchedule.query.all()
    return render_template('admin/activity_htmx_table.html',
                            activities=activities,
                            schedules=schedules)


# affiche le tableau des activités spécifique pour les membres de l'équipe
@admin_activity_bp.route('/admin/activity/table_staff')
def display_activity_table_staff():
    activities = Activity.query.filter_by(is_staff=True).all()
    schedules = ActivitySchedule.query.all()
    staff = Pharmacist.query.all()
    return render_template('admin/activity_htmx_table.html',
                            staff=staff,
                            activities=activities,
                            schedules=schedules)


# mise à jour des informations d'une activité 
@admin_activity_bp.route('/admin/activity/activity_update/<int:activity_id>', methods=['POST'])
def update_activity(activity_id):
    activity = Activity.query.get(activity_id)
    old_schedules = activity.schedules

    if activity:
        if request.form.get('name') == '':
            app.display_toast(success=False, message="Le nom est obligatoire")
            return ""
        if request.form.get('letter') == '':
            app.display_toast(success=False, message="La lettre est obligatoire")
            return ""
        activity.name = request.form.get('name', activity.name)
        activity.letter = request.form.get('letter', activity.letter)
        activity.inactivity_message = request.form.get('inactivity_message', activity.inactivity_message)
        activity.specific_message = request.form.get('specific_message', activity.specific_message)
        activity.notification = True if request.form.get('notification', activity.notification) == "true" else False

        # Mettre à jour les horaires
        schedule_ids = request.form.getlist('schedules')  # Cela devrait retourner une liste de IDs
        activity.schedules = [ActivitySchedule.query.get(int(id)) for id in schedule_ids]
        update_scheduler_for_activity(activity)
        
        # Si on a modifier les schedules, on met à jour le bouton
        if activity.schedules != old_schedules:
            update_bouton_after_scheduler_changed(activity)

        if request.form.get("staff_id"):
            activity.is_staff = True
            activity.staff = Pharmacist.query.get(int(request.form.get("staff_id")))
        else:
            activity.is_staff = False

        db.session.commit()
        app.display_toast(success=True, message="Activité ajoutée avec succès")
        return ""
    else:
        return app.display_toast(success=False, message="Activité introuvable")


def update_bouton_after_scheduler_changed(activity):
    """ Si on modifie le scheduler d'une activité, il faut vérifier où en est le bouton.
    Il faut donc éventuellement remettre le bouton en activité ou au contraire le rendre inactif."""
    # Obtenir l'heure actuelle et le jour actuel
    current_time = datetime.now().time()
    current_weekday = datetime.now().strftime('%A')  # Renvoie le jour de la semaine en anglais
    print(current_weekday, current_time)

    # Charger l'activité avec ses horaires et boutons associés
    activity = Activity.query.options(
        joinedload(Activity.schedules).joinedload(ActivitySchedule.weekdays),
        joinedload(Activity.buttons)
    ).filter_by(id=activity.id).first()

    if not activity:
        print(f"Activity with id {activity.id} not found.")
        return

    # Initialiser le drapeau d'activité à False
    is_activity_active = False

    # Parcourir les créneaux horaires de l'activité
    for schedule in activity.schedules:
        print(schedule)
        for weekday in schedule.weekdays:
            print(weekday.english_name)
            if weekday.english_name.lower() == current_weekday.lower():
                if schedule.start_time <= current_time <= schedule.end_time:
                    is_activity_active = True
                    break
        if is_activity_active:
            break

    # Mettre à jour les boutons associés à l'activité
    for button in activity.buttons:
        if button.is_active != is_activity_active:
            button.is_active = is_activity_active
            db.session.add(button)  # Ajouter le bouton à la session pour la mise à jour
            app.display_toast(success=True, message=f"Le bouton '{button.label} 'vient de changer d'activité.")

    db.session.commit()  # Sauvegarder les modifications dans la base de données

    app.logger.info(f"UPDATE BOUTON: Activity {activity.name} is_active={is_activity_active}")


# affiche la modale pour confirmer la suppression d'une activité
@admin_activity_bp.route('/admin/activity/confirm_delete/<int:activity_id>', methods=['GET'])
def confirm_delete_activity(activity_id):
    activity = Activity.query.get(activity_id)
    return render_template('/admin/activity_modal_confirm_delete.html', activity=activity)


# affiche la modale pour confirmer la suppression d'une activité quand c'est un membre de l'équipe
@admin_activity_bp.route('/admin/activity/confirm_delete/staff/<int:activity_id>', methods=['GET'])
def confirm_delete_activity_staff(activity_id):
    activity = Activity.query.get(activity_id)
    return render_template('/admin/activity_modal_confirm_delete.html', activity=activity, staff=True)


# supprime un membre de l'equipe
@admin_activity_bp.route('/admin/activity/delete/<int:activity_id>', methods=['GET'])
def delete_activity(activity_id, staff=None):
    try:
        activity = Activity.query.get(activity_id)
        if not activity:
            app.display_toast(success=False, message="Activité non trouvée")
            return return_good_display_activity(staff)

        db.session.delete(activity)
        db.session.commit()
        app.display_toast(success=True, message="Activité supprimée avec succès")
        return return_good_display_activity(staff)

    except Exception as e:
        db.session.rollback()
        app.logger.error(str(e))
        app.display_toast(success=False, message="erreur : " + str(e))
        return return_good_display_activity(staff)


@admin_activity_bp.route('/admin/activity/delete/staff/<int:activity_id>', methods=['GET'])
def delete_activity_staff(activity_id, staff=None):
    return delete_activity(activity_id, staff=True)

# affiche le formulaire pour ajouter un activité
@admin_activity_bp.route('/admin/activity/add_form')
def add_activity_form():
    schedules = ActivitySchedule.query.all()
    return render_template('/admin/activity_add_form.html', schedules=schedules)


# affiche le formulaire pour ajouter un activité lié à un membre de l'équipe
@admin_activity_bp.route('/admin/activity/add_staff_form')
def add_activity_staff_form():

    print(Pharmacist.query.all())
    return render_template('/admin/activity_add_form.html', 
                            schedules=ActivitySchedule.query.all(),
                            staff=Pharmacist.query.all())


# enregistre l'activité' dans la Bdd
@admin_activity_bp.route('/admin/activity/add_new_activity', methods=['POST'])
def add_new_activity():
    try:
        name = request.form.get('name')
        letter = request.form.get('letter')
        schedule_ids = request.form.getlist('schedules')
        inactivity_message = request.form.get('inactivity_message')
        specific_message = request.form.get('specific_message')
        notification = True if request.form.get('notification') == "true" else False
        staff_id = request.form.get("staff_id")

        
        if not name:  # Vérifiez que les champs obligatoires sont remplis
            return return_good_display_activity(staff_id)

        new_activity = Activity(
            name=name,
            letter=letter,
            inactivity_message=inactivity_message,
            specific_message=specific_message,
            notification=notification
        )
        
        if staff_id:
            new_activity.is_staff = True
            new_activity.staff = Pharmacist.query.get(int(staff_id))
            
        db.session.add(new_activity)

        db.session.commit()

        for schedule_id in schedule_ids:
            schedule = ActivitySchedule.query.get(int(schedule_id))
            if schedule:
                new_activity.schedules.append(schedule)
        db.session.commit()

        for schedule_id in schedule_ids:
            schedule = ActivitySchedule.query.get(int(schedule_id))
            app.scheduler.add_job(func=update_button_presence, args=[new_activity.id, True, app],
                            trigger="cron", day_of_week='mon-sun', 
                            hour=schedule.start_time.hour, minute=schedule.start_time.minute,
                            id=f'activate_activity{new_activity.id}_schedule{schedule.id}')
            app.scheduler.add_job(func=update_button_presence, args=[new_activity.id, False, app],
                            trigger="cron", day_of_week='mon-sun', 
                            hour=schedule.end_time.hour, minute=schedule.end_time.minute,
                            id=f'desactivate_activity{new_activity.id}_schedule{schedule.id}')

        print("communication", staff_id)
        if staff_id:
            print("staff_id", staff_id)
            app.communication("update_admin", data={"action":"delete_add_activity_form_staff"})
        else:
            app.communication("update_admin", data={"action":"delete_add_activity_form"})

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_staff_form"></div>"""

        return f"{return_good_display_activity(staff_id)}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message="erreur : " + str(e))
        return return_good_display_activity(staff_id)


def return_good_display_activity(staff):
    """ Sert uniquement à retourner le bon affichage entre activité et activité == équipier"""
    if staff:
        print("staff", staff)
        return display_activity_table_staff()
    else:
        return display_activity_table()
    

def update_button_presence(activity_id, is_present, app):
    with app.app_context():  # Crée un contexte d'application
        try:
            buttons = Button.query.order_by(Button.sort_order).filter_by(activity_id=activity_id).all()
            for button in buttons:
                button.is_present = is_present
            db.session.commit()
            print(f"Buttons for activity {activity_id} set to {'present' if is_present else 'not present'}")
        except Exception as e:
            print(f"Failed to update button presence: {str(e)}")
            db.session.rollback()

def update_scheduler_for_activity(activity):
    # Constantes pour la configuration
    MISFIRE_GRACE_TIME = 300  # 5 minutes de délai de grâce
    
    job_id_disable_prefix = f"disable_{activity.name}_"
    job_id_enable_prefix = f"enable_{activity.name}_"

    # Nettoyage des jobs existants
    try:
        for job in app.scheduler.get_jobs():
            if job.id.startswith(job_id_disable_prefix) or job.id.startswith(job_id_enable_prefix):
                app.scheduler.remove_job(job.id)
                app.logger.info(f"Removed existing job: {job.id}")
    except Exception as e:
        app.logger.error(f"Error removing existing jobs for {activity.name}: {str(e)}")

    def is_full_day(start_time, end_time):
        return start_time == time(0, 0) and end_time == time(23, 59)
    
    def is_full_week(weekdays):
        all_days = {'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'}
        active_days = {day.abbreviation.strip().lower() for day in weekdays}
        return active_days == all_days

    def add_job(job_id, func, args, trigger_args):
        try:
            app.scheduler.add_job(
                id=job_id,
                func=func,
                args=args,
                trigger='cron',
                misfire_grace_time=MISFIRE_GRACE_TIME,
                coalesce=True,
                max_instances=1,
                **trigger_args
            )
            app.logger.info(f"Successfully added job: {job_id}")
        except Exception as e:
            app.logger.error(f"Failed to add job {job_id}: {str(e)}")

    for schedule in activity.schedules:
        full_day = is_full_day(schedule.start_time, schedule.end_time)
        full_week = is_full_week(schedule.weekdays)

        if full_day and full_week:
            app.logger.info(f"Full day and full week: No jobs created for {activity.name}")
            continue
        
        if full_day:
            start_day = min(schedule.weekdays, key=lambda x: x.id).abbreviation.strip().lower()
            end_day = max(schedule.weekdays, key=lambda x: x.id).abbreviation.strip().lower()

            add_job(
                job_id=f"{job_id_enable_prefix}{start_day}",
                func='scheduler_functions:enable_buttons_for_activity_job',
                args=[activity.id],
                trigger_args={
                    'day_of_week': start_day,
                    'hour': 0,
                    'minute': 0
                }
            )
            add_job(
                job_id=f"{job_id_disable_prefix}{end_day}",
                func='scheduler_functions:disable_buttons_for_activity_job',
                args=[activity.id],
                trigger_args={
                    'day_of_week': end_day,
                    'hour': 23,
                    'minute': 59
                }
            )
            app.logger.info(f"Scheduled full-day jobs for activity {activity.name} from {start_day} to {end_day}")
            continue
        
        for weekday in schedule.weekdays:
            day = weekday.abbreviation.strip().lower()

            add_job(
                job_id=f"{job_id_enable_prefix}{day}_{schedule.start_time.strftime('%H%M')}",
                func='scheduler_functions:enable_buttons_for_activity_job',
                args=[activity.id],
                trigger_args={
                    'day_of_week': day,
                    'hour': schedule.start_time.hour,
                    'minute': schedule.start_time.minute
                }
            )
            add_job(
                job_id=f"{job_id_disable_prefix}{day}_{schedule.end_time.strftime('%H%M')}",
                func='scheduler_functions:disable_buttons_for_activity_job',
                args=[activity.id],
                trigger_args={
                    'day_of_week': day,
                    'hour': schedule.end_time.hour,
                    'minute': schedule.end_time.minute
                }
            )
            app.logger.info(
                f"Scheduled jobs for {activity.name} on {day}: "
                f"enable at {schedule.start_time.strftime('%H:%M')}, "
                f"disable at {schedule.end_time.strftime('%H:%M')}"
            )

