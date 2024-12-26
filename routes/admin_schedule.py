from flask import Blueprint, render_template, request, current_app as app
from models import ActivitySchedule, Activity, Weekday, activity_schedule_link, db
from utils import parse_time
from routes.admin_activity import update_bouton_after_scheduler_changed
from routes.admin_security import require_permission
from communication import communikation

admin_schedule_bp = Blueprint('admin_schedule', __name__)

# affiche le tableau des plages horaires
@admin_schedule_bp.route('/admin/schedule/table')
def display_schedule_table():
    schedules = ActivitySchedule.query.all()
    weekdays = Weekday.query.all()
    return render_template('admin/schedule_htmx_table.html',
                            schedules=schedules,
                            weekdays=weekdays)


# mise à jour des informations d'une activité 
@admin_schedule_bp.route('/admin/schedule/schedule_update/<int:schedule_id>', methods=['POST'])
def update_schedule(schedule_id):
    try:
        schedule = ActivitySchedule.query.get(schedule_id)
        if schedule:
            schedule.name = request.form.get('name_schedule', schedule.name)
            start_time_str = request.form.get('start_time')
            end_time_str = request.form.get('end_time')
            schedule.start_time = parse_time(start_time_str) if start_time_str else schedule.start_time
            schedule.end_time = parse_time(end_time_str) if end_time_str else schedule.end_time

            # Mettre à jour les horaires
            weekdays_ids = request.form.getlist('weekdays')  # Cela devrait retourner une liste de IDs
            schedule.weekdays = [Weekday.query.get(int(id)) for id in weekdays_ids]

            db.session.commit()
            app.display_toast(success=True, message="Plage horaire mise à jour")

            # Mise à jour des boutons des activités qui dépendent du schedule
            activities_with_this_schedule = Activity.query.join(activity_schedule_link).filter(
                activity_schedule_link.c.schedule_id == schedule_id
            ).all()
            print("activities_with_this_schedule", activities_with_this_schedule)
            for activity in activities_with_this_schedule:
                update_bouton_after_scheduler_changed(activity)

            # mise à jour de la table activité si nouvelle plage horaire
            communikation("admin", event="refresh_activity_table")

            return ""
        else:
            app.display_toast(success=False, message="Plage horaire introuvable")
            return ""

    except Exception as e:
        app.logger.error(str(e))
        app.display_toast(success = False, message=str(e))
        return ""


# affiche le formulaire pour ajouter un activité
@admin_schedule_bp.route('/admin/schedule/add_form')
def add_schedule_form():
    weekdays = Weekday.query.all()
    return render_template('/admin/schedule_add_form.html', weekdays=weekdays)


# enregistre l'activité' dans la Bdd
@admin_schedule_bp.route('/admin/schedule/add_new_schedule', methods=['POST'])
def add_new_schedule():
    try:
        name = request.form.get('name_schedule')
        start_time_str = request.form.get('start_time')
        end_time_str = request.form.get('end_time')
        start_time = parse_time(start_time_str)
        end_time = parse_time(end_time_str)

        if not name:  # Vérifiez que les champs obligatoires sont remplis
            app.display_toast(success=False, message="Nom obligatoire")
            return display_schedule_table()

        new_schedule = ActivitySchedule(
            name=name,
            start_time=start_time,
            end_time=end_time)

        db.session.add(new_schedule)

        db.session.commit()

        weekdays_ids = request.form.getlist('weekdays')  # Cela devrait retourner une liste de IDs
        for weekdays_id in weekdays_ids:
            weekday = Weekday.query.get(int(weekdays_id))
            if weekday:
                new_schedule.weekdays.append(weekday)
        db.session.commit()

        # mise à jour de la table activité si nouvelle plage horaire
        communikation("admin", event="refresh_activity_table")
        
        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_schedule_form"></div>"""

        return f"{display_schedule_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message="erreur : " + str(e))
        return display_schedule_table()
    

# affiche la modale pour confirmer la suppression d'une plage horaire
@admin_schedule_bp.route('/admin/schedule/confirm_delete/<int:schedule_id>', methods=['GET'])
def confirm_delete_schedule(schedule_id):
    schedule = ActivitySchedule.query.get(schedule_id)
    return render_template('/admin/schedule_modal_confirm_delete.html', schedule=schedule)


@admin_schedule_bp.route('/admin/schedule/delete/<int:schedule_id>', methods=['GET'])
def delete_schedule(schedule_id):
    try:
        schedule = ActivitySchedule.query.get(schedule_id)
        if not schedule:
            app.display_toast(success=False, message="Plage horaire introuvable")
            return display_schedule_table()

        db.session.delete(schedule)
        db.session.commit()
        app.display_toast(success=True, message="Suppression réussie'")

        # mise à jour de la table activité si nouvelle plage horaire
        communikation("admin", event="refresh_activity_table")

        return display_schedule_table()

    except Exception as e:
        db.session.rollback()
        app.display_toast(success=False, message="erreur : " + str(e))
        return display_schedule_table()
