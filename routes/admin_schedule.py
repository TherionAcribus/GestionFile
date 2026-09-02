from flask import Blueprint, render_template, request, current_app as app
from models import ActivitySchedule, Activity, Weekday, activity_schedule_link, db
from utils import parse_time
from routes.admin_activity import update_bouton_after_scheduler_changed
from routes.admin_security import require_permission
from communication import communikation
from form_validation import Champ, LISTE_ENTIERS, extraire, valider
from transactions import atomic
from ui_feedback import display_toast

admin_schedule_bp = Blueprint('admin_schedule', __name__)

# affiche le tableau des plages horaires
@admin_schedule_bp.route('/admin/schedule/table')
@require_permission('schedule')
def display_schedule_table():
    schedules = ActivitySchedule.query.all()
    weekdays = Weekday.query.all()
    return render_template('admin/schedule_htmx_table.html',
                            schedules=schedules,
                            weekdays=weekdays)


# mise à jour des informations d'une activité 
@admin_schedule_bp.route('/admin/schedule/schedule_update/<int:schedule_id>', methods=['POST'])
@require_permission('schedule')
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
            display_toast(success=True, message="Plage horaire mise à jour")

            # Mise à jour des boutons des activités qui dépendent du schedule
            activities_with_this_schedule = Activity.query.join(activity_schedule_link).filter(
                activity_schedule_link.c.schedule_id == schedule_id
            ).all()
            app.logger.debug('activities_with_this_schedule %s', activities_with_this_schedule)
            for activity in activities_with_this_schedule:
                update_bouton_after_scheduler_changed(activity)

            # mise à jour de la table activité si nouvelle plage horaire
            communikation("admin", event="refresh_activity_table")

            return ""
        else:
            display_toast(success=False, message="Plage horaire introuvable")
            return ""

    except Exception as e:
        app.logger.error(str(e))
        display_toast(success = False, message=str(e))
        return ""


# affiche le formulaire pour ajouter un activité
@admin_schedule_bp.route('/admin/schedule/add_form')
@require_permission('schedule')
def add_schedule_form():
    weekdays = Weekday.query.all()
    return render_template('/admin/schedule_add_form.html', weekdays=weekdays)


#: Formulaire de creation d'une plage horaire (point 5).
SCHEMA_HORAIRE = (
    Champ("name_schedule", obligatoire=True, libelle="Le nom", longueur_max=50),
    Champ("start_time", libelle="L'heure de debut"),
    Champ("end_time", libelle="L'heure de fin"),
    Champ("weekdays", type=LISTE_ENTIERS, libelle="Les jours"),
)


# enregistre l'activité' dans la Bdd
@admin_schedule_bp.route('/admin/schedule/add_new_schedule', methods=['POST'])
@require_permission('schedule')
def add_new_schedule():
    try:
        valeurs, erreurs = valider(
            extraire(SCHEMA_HORAIRE, request.form.get, request.form.getlist),
            SCHEMA_HORAIRE,
        )
        if erreurs:
            display_toast(success=False, message=erreurs[0])
            return display_schedule_table()

        start_time = parse_time(valeurs["start_time"])
        end_time = parse_time(valeurs["end_time"])

        # Point 6 : creation + rattachement des jours dans UNE transaction.
        with atomic():
            new_schedule = ActivitySchedule(
                name=valeurs["name_schedule"],
                start_time=start_time,
                end_time=end_time)
            db.session.add(new_schedule)
            db.session.flush()

            for weekdays_id in valeurs["weekdays"]:
                weekday = Weekday.query.get(weekdays_id)
                if weekday:
                    new_schedule.weekdays.append(weekday)

        # mise à jour de la table activité si nouvelle plage horaire
        communikation("admin", event="refresh_activity_table")
        
        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_schedule_form"></div>"""

        return f"{display_schedule_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        return display_schedule_table()
    

# affiche la modale pour confirmer la suppression d'une plage horaire
@admin_schedule_bp.route('/admin/schedule/confirm_delete/<int:schedule_id>', methods=['GET'])
@require_permission('schedule')
def confirm_delete_schedule(schedule_id):
    schedule = ActivitySchedule.query.get(schedule_id)
    return render_template('/admin/schedule_modal_confirm_delete.html', schedule=schedule)


@admin_schedule_bp.route('/admin/schedule/delete/<int:schedule_id>', methods=['DELETE'])
@require_permission('schedule')
def delete_schedule(schedule_id):
    try:
        schedule = ActivitySchedule.query.get(schedule_id)
        if not schedule:
            display_toast(success=False, message="Plage horaire introuvable")
            return display_schedule_table()

        db.session.delete(schedule)
        db.session.commit()
        display_toast(success=True, message="Suppression réussie'")

        # mise à jour de la table activité si nouvelle plage horaire
        communikation("admin", event="refresh_activity_table")

        return display_schedule_table()

    except Exception as e:
        db.session.rollback()
        display_toast(success=False, message="erreur : " + str(e))
        return display_schedule_table()
