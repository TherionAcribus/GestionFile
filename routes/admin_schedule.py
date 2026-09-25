from flask import Blueprint, render_template, request, current_app as app
from sqlalchemy.orm import selectinload
from models import ActivitySchedule, Activity, Weekday, activity_schedule_link, db
from utils import parse_time
from routes.admin_activity import update_bouton_after_scheduler_changed, update_scheduler_for_activity
from routes.admin_security import require_permission
from communication import communikation
from form_validation import Champ, LISTE_ENTIERS, extraire, valider
from transactions import atomic
from ui_feedback import display_toast
from activity_explain import describe_schedule
from audit_service import record_audit
from audit_log import (
    ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE,
    OUTCOME_FAILURE, OUTCOME_SUCCESS,
)

admin_schedule_bp = Blueprint('admin_schedule', __name__)


def _activities_using(schedule_id):
    return Activity.query.join(activity_schedule_link).filter(
        activity_schedule_link.c.schedule_id == schedule_id
    ).all()


def _replan_activities(activities):
    """Après un changement de plage : replanifie les tâches d'ouverture /
    fermeture ET remet les boutons dans le bon état. Auparavant seuls les
    boutons étaient corrigés — les tâches gardaient les anciennes heures."""
    for activity in activities:
        update_scheduler_for_activity(activity)
        update_bouton_after_scheduler_changed(activity)


# affiche la liste des plages horaires
@admin_schedule_bp.route('/admin/schedule/table')
@require_permission('schedule')
def display_schedule_table():
    schedules = (ActivitySchedule.query
                 .options(selectinload(ActivitySchedule.weekdays),
                          selectinload(ActivitySchedule.activities))
                 .order_by(ActivitySchedule.start_time, ActivitySchedule.name)
                 .all())
    items = [{"schedule": s, "summary": describe_schedule(s)} for s in schedules]
    return render_template('admin/schedule_htmx_table.html',
                            items=items,
                            weekdays=Weekday.query.order_by(Weekday.id).all())


#: Formulaire d'une plage horaire — creation ET modification (point 5).
SCHEMA_HORAIRE = (
    Champ("name_schedule", obligatoire=True, libelle="Le nom", longueur_max=50),
    Champ("start_time", obligatoire=True, libelle="L'heure de debut"),
    Champ("end_time", obligatoire=True, libelle="L'heure de fin"),
    Champ("weekdays", type=LISTE_ENTIERS, libelle="Les jours"),
)


def _valider_horaire(form):
    """``(valeurs, erreur)`` : heures converties, début < fin, au moins un jour.

    Une plage « 23:00–02:00 » n'est pas gérée par les horaires d'activité
    (comparaison début <= heure <= fin) : elle est refusée plutôt que de ne
    jamais s'ouvrir.
    """
    valeurs, erreurs = valider(
        extraire(SCHEMA_HORAIRE, form.get, form.getlist), SCHEMA_HORAIRE)
    if erreurs:
        return None, erreurs[0]
    start, end = parse_time(valeurs["start_time"]), parse_time(valeurs["end_time"])
    if start is None or end is None:
        return None, "Format d'heure invalide (HH:MM attendu)."
    if start >= end:
        return None, "L'heure de fin doit être après l'heure de début."
    if not valeurs["weekdays"]:
        return None, "Cochez au moins un jour."
    valeurs["start_time"], valeurs["end_time"] = start, end
    return valeurs, None


# mise à jour d'une plage horaire
@admin_schedule_bp.route('/admin/schedule/schedule_update/<int:schedule_id>', methods=['POST'])
@require_permission('schedule')
def update_schedule(schedule_id):
    schedule = db.session.get(ActivitySchedule, schedule_id)
    if schedule is None:
        return display_toast(success=False, message="Plage horaire introuvable")

    valeurs, erreur = _valider_horaire(request.form)
    if erreur:
        # 204 : la liste n'est pas remplacée, la saisie est conservée.
        return display_toast(success=False, message=erreur)

    try:
        schedule.name = valeurs["name_schedule"]
        schedule.start_time = valeurs["start_time"]
        schedule.end_time = valeurs["end_time"]
        schedule.weekdays = [w for w in (db.session.get(Weekday, wid)
                                         for wid in valeurs["weekdays"]) if w]
        db.session.commit()
    except Exception:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "schedule", target_id=schedule_id,
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de la mise a jour d'une plage horaire")
        return display_toast(success=False, message="La mise à jour a échoué.")

    record_audit(ACTION_UPDATE, "schedule", target_id=schedule_id,
                 outcome=OUTCOME_SUCCESS,
                 details=f"name={schedule.name}")
    _replan_activities(_activities_using(schedule_id))
    display_toast(success=True, message="Plage horaire enregistrée")

    # Les listes d'activités affichent le résumé des plages : rechargement.
    communikation("admin", event="refresh_activity_table")
    return display_schedule_table()


# affiche le formulaire pour ajouter une plage horaire
@admin_schedule_bp.route('/admin/schedule/add_form')
@require_permission('schedule')
def add_schedule_form():
    weekdays = Weekday.query.order_by(Weekday.id).all()
    return render_template('/admin/schedule_add_form.html', weekdays=weekdays)


# enregistre la plage horaire dans la Bdd
@admin_schedule_bp.route('/admin/schedule/add_new_schedule', methods=['POST'])
@require_permission('schedule')
def add_new_schedule():
    try:
        valeurs, erreur = _valider_horaire(request.form)
        if erreur:
            # 204 : rien n'est remplacé, la saisie reste dans le formulaire.
            return display_toast(success=False, message=erreur)

        # Point 6 : creation + rattachement des jours dans UNE transaction.
        with atomic():
            new_schedule = ActivitySchedule(
                name=valeurs["name_schedule"],
                start_time=valeurs["start_time"],
                end_time=valeurs["end_time"])
            db.session.add(new_schedule)
            db.session.flush()

            for weekdays_id in valeurs["weekdays"]:
                weekday = Weekday.query.get(weekdays_id)
                if weekday:
                    new_schedule.weekdays.append(weekday)

        record_audit(ACTION_CREATE, "schedule", target_id=new_schedule.id,
                     outcome=OUTCOME_SUCCESS,
                     details=f"name={new_schedule.name}")
        display_toast(success=True, message="Plage horaire créée")
        # mise à jour de la table activité si nouvelle plage horaire
        communikation("admin", event="refresh_activity_table")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_schedule_form"></div>"""

        return f"{display_schedule_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_CREATE, "schedule",
                     target_id=request.form.get('name_schedule'),
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de l'ajout d'une plage horaire")
        return display_toast(success=False, message="L'ajout a échoué.")


# affiche la modale pour confirmer la suppression d'une plage horaire
@admin_schedule_bp.route('/admin/schedule/confirm_delete/<int:schedule_id>', methods=['GET'])
@require_permission('schedule')
def confirm_delete_schedule(schedule_id):
    schedule = ActivitySchedule.query.get(schedule_id)
    return render_template('/admin/schedule_modal_confirm_delete.html', schedule=schedule,
                           activities=_activities_using(schedule_id))


@admin_schedule_bp.route('/admin/schedule/delete/<int:schedule_id>', methods=['DELETE'])
@require_permission('schedule')
def delete_schedule(schedule_id):
    try:
        schedule = ActivitySchedule.query.get(schedule_id)
        if not schedule:
            display_toast(success=False, message="Plage horaire introuvable")
            return display_schedule_table()

        affected = _activities_using(schedule_id)
        db.session.delete(schedule)
        db.session.commit()
        record_audit(ACTION_DELETE, "schedule", target_id=schedule_id,
                     outcome=OUTCOME_SUCCESS)
        # Les activités qui utilisaient cette plage perdent ce créneau : leurs
        # tâches planifiées et leurs boutons doivent suivre.
        _replan_activities(affected)
        display_toast(success=True, message="Plage horaire supprimée")

        # mise à jour de la table activité si nouvelle plage horaire
        communikation("admin", event="refresh_activity_table")

        return display_schedule_table()

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_DELETE, "schedule", target_id=schedule_id,
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de la suppression d'une plage horaire")
        display_toast(success=False, message="La suppression a échoué.")
        return display_schedule_table()
