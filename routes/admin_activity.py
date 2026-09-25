from datetime import datetime, time
from flask import Blueprint, render_template, request, current_app as app
from models import Activity, ActivitySchedule, Pharmacist, Button, db
from sqlalchemy.orm import joinedload, selectinload
from routes.admin_security import require_permission
from form_validation import Champ, BOOLEEN, ENTIER, LISTE_ENTIERS, extraire, valider
from transactions import atomic
from ui_feedback import display_toast
from audit_service import record_audit
from audit_log import (
    ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE,
    OUTCOME_FAILURE, OUTCOME_SUCCESS,
)
from activity_explain import describe_schedule, is_continuous, is_open_at, shared_letters
from extensions import scheduler

admin_activity_bp = Blueprint('admin_activity', __name__)

VALID_TABS = ('activity', 'staff', 'schedule')


def _now():
    """Heure de référence des horaires : la même que celle utilisée pour
    (dés)activer les boutons (update_bouton_after_scheduler_changed)."""
    return datetime.now()


# page de base
@admin_activity_bp.route('/admin/activity')
@require_permission('activity')
def admin_activity():
    # ?tab= était lu puis ignoré : l'onglet demandé est désormais ouvert.
    tab = request.args.get('tab', 'activity')
    if tab not in VALID_TABS:
        tab = 'activity'
    return render_template('/admin/activity.html', active_tab=tab)


def _schedules_with_summary():
    schedules = ActivitySchedule.query.options(
        selectinload(ActivitySchedule.weekdays)).all()
    return [{"schedule": s, "summary": describe_schedule(s)} for s in schedules]


def _render_activity_list(is_staff):
    """Liste des activités (ou des demandes « équipier ») en cartes."""
    # Le gabarit lit activity.schedules pour chaque activité : selectinload
    # charge les horaires en une requête IN groupée (évite un N+1).
    activities = (Activity.query
                  .options(selectinload(Activity.schedules)
                           .selectinload(ActivitySchedule.weekdays))
                  .filter_by(is_staff=is_staff)
                  .order_by(Activity.letter, Activity.name)
                  .all())
    # Doublons de lettre cherchés sur TOUTES les activités (équipier compris).
    shared = shared_letters(Activity.query.all())
    now = _now()
    weekday = now.strftime('%A')
    items = [
        {
            "activity": activity,
            "continuous": is_continuous(activity.schedules),
            "open": is_open_at(activity.schedules, weekday, now.time()),
            "schedules": [describe_schedule(s) for s in activity.schedules],
            "shared_letter": shared.get(activity.id, []),
        }
        for activity in activities
    ]
    return render_template('admin/activity_htmx_table.html',
                           items=items,
                           is_staff=is_staff,
                           schedules=_schedules_with_summary(),
                           staff=Pharmacist.query.all() if is_staff else None)


# affiche la liste des activités
@admin_activity_bp.route('/admin/activity/table')
@require_permission('activity')
def display_activity_table():
    return _render_activity_list(is_staff=False)


# affiche la liste des activités « équipier » (demandes pour un membre précis)
@admin_activity_bp.route('/admin/activity/table_staff')
@require_permission('activity')
def display_activity_table_staff():
    return _render_activity_list(is_staff=True)


#: Formulaire d'une activite — creation ET modification (point 5).
SCHEMA_ACTIVITE = (
    Champ("name", obligatoire=True, libelle="Le nom", longueur_max=100),
    Champ("letter", obligatoire=True, libelle="La lettre", longueur_max=1),
    Champ("inactivity_message", libelle="Le message d'inactivite", defaut="",
          longueur_max=255),
    Champ("specific_message", libelle="Le message specifique", defaut="",
          longueur_max=255),
    Champ("notification", type=BOOLEEN, libelle="La notification"),
    Champ("staff_id", type=ENTIER, libelle="Le membre d'equipe"),
    Champ("schedules", type=LISTE_ENTIERS, libelle="Les plages horaires"),
    # « always » (défaut du formulaire) : proposée en continu, aucune plage.
    # Absent (restauration, anciens clients) : les plages envoyées font foi.
    Champ("availability", libelle="La disponibilité", choix=("always", "schedules")),
)


def _valider_activite(form):
    """``(valeurs, erreur)`` : schéma commun + lettre normalisée en majuscule."""
    valeurs, erreurs = valider(
        extraire(SCHEMA_ACTIVITE, form.get, form.getlist), SCHEMA_ACTIVITE)
    if erreurs:
        return None, erreurs[0]
    letter = valeurs["letter"].strip().upper()
    if not letter.isalnum():
        return None, "La lettre doit être une lettre ou un chiffre."
    valeurs["letter"] = letter
    if valeurs.get("availability") == "always":
        valeurs["schedules"] = []
    elif valeurs.get("availability") == "schedules" and not valeurs["schedules"]:
        return None, "Cochez au moins une plage horaire, ou choisissez « En continu »."
    return valeurs, None


# mise à jour des informations d'une activité
@admin_activity_bp.route('/admin/activity/activity_update/<int:activity_id>', methods=['POST'])
@require_permission('activity')
def update_activity(activity_id):
    activity = db.session.get(Activity, activity_id)
    if activity is None:
        return display_toast(success=False, message="Activité introuvable")

    valeurs, erreur = _valider_activite(request.form)
    if erreur:
        # 204 : la liste n'est pas remplacée, la saisie reste dans le formulaire.
        return display_toast(success=False, message=erreur)

    previous_name = activity.name
    previous_schedule_ids = {s.id for s in activity.schedules}
    try:
        activity.name = valeurs["name"]
        activity.letter = valeurs["letter"]
        activity.inactivity_message = valeurs["inactivity_message"]
        activity.specific_message = valeurs["specific_message"]
        activity.notification = valeurs["notification"]
        # Plages inconnues ignorées (l'ancien code pouvait insérer None).
        activity.schedules = [s for s in (db.session.get(ActivitySchedule, sid)
                                          for sid in valeurs["schedules"]) if s]

        if activity.is_staff:
            staff = db.session.get(Pharmacist, valeurs["staff_id"]) if valeurs["staff_id"] else None
            if staff is None:
                db.session.rollback()
                return display_toast(success=False, message="Choisissez le membre de l'équipe.")
            activity.staff = staff

        db.session.commit()
    except Exception:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "activity", target_id=activity_id,
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de la mise a jour d'une activite")
        return display_toast(success=False, message="La mise à jour a échoué.")

    # Tâches planifiées : les identifiants contiennent le nom — un
    # renommage laissait les anciennes tâches actives.
    update_scheduler_for_activity(activity, previous_name=previous_name)
    if {s.id for s in activity.schedules} != previous_schedule_ids:
        update_bouton_after_scheduler_changed(activity)

    record_audit(ACTION_UPDATE, "activity", target_id=activity_id,
                 outcome=OUTCOME_SUCCESS,
                 details=f"name={activity.name}")
    display_toast(success=True, message="Activité enregistrée")
    return return_good_display_activity(activity.is_staff)


def update_bouton_after_scheduler_changed(activity):
    """ Si on modifie le scheduler d'une activité, il faut vérifier où en est le bouton.
    Il faut donc éventuellement remettre le bouton en activité ou au contraire le rendre inactif."""
    now = _now()

    # Charger l'activité avec ses horaires et boutons associés
    activity = Activity.query.options(
        joinedload(Activity.schedules).joinedload(ActivitySchedule.weekdays),
        joinedload(Activity.buttons)
    ).filter_by(id=activity.id).first()

    if not activity:
        return

    # Même règle que l'affichage « Dans ses horaires » de la page admin.
    is_activity_active = is_open_at(activity.schedules, now.strftime('%A'), now.time())

    # Mettre à jour les boutons associés à l'activité
    for button in activity.buttons:
        if button.is_active != is_activity_active:
            button.is_active = is_activity_active
            db.session.add(button)  # Ajouter le bouton à la session pour la mise à jour
            display_toast(success=True, message=f"Le bouton « {button.label} » vient de changer d'état.")

    db.session.commit()  # Sauvegarder les modifications dans la base de données

    app.logger.info(f"UPDATE BOUTON: Activity {activity.name} is_active={is_activity_active}")


# affiche la modale pour confirmer la suppression d'une activité
@admin_activity_bp.route('/admin/activity/confirm_delete/<int:activity_id>', methods=['GET'])
@require_permission('activity')
def confirm_delete_activity(activity_id):
    activity = Activity.query.get(activity_id)
    return render_template('/admin/activity_modal_confirm_delete.html', activity=activity,
                           buttons_count=Button.query.filter_by(activity_id=activity_id).count())


# affiche la modale pour confirmer la suppression d'une activité quand c'est un membre de l'équipe
@admin_activity_bp.route('/admin/activity/confirm_delete/staff/<int:activity_id>', methods=['GET'])
@require_permission('activity')
def confirm_delete_activity_staff(activity_id):
    activity = Activity.query.get(activity_id)
    return render_template('/admin/activity_modal_confirm_delete.html', activity=activity, staff=True,
                           buttons_count=Button.query.filter_by(activity_id=activity_id).count())


# supprime une activité
@admin_activity_bp.route('/admin/activity/delete/<int:activity_id>', methods=['DELETE'])
@require_permission('activity')
def delete_activity(activity_id, staff=None):
    try:
        activity = Activity.query.get(activity_id)
        if not activity:
            display_toast(success=False, message="Activité non trouvée")
            return return_good_display_activity(staff)

        name = activity.name
        db.session.delete(activity)
        db.session.commit()
        # Sans cela, les tâches d'ouverture/fermeture continuaient de tourner
        # (en échec) pour une activité qui n'existe plus.
        remove_activity_jobs(name)
        record_audit(ACTION_DELETE, "activity", target_id=activity_id,
                     outcome=OUTCOME_SUCCESS)
        display_toast(success=True, message="Activité supprimée avec succès")
        return return_good_display_activity(staff)

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_DELETE, "activity", target_id=activity_id,
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de la suppression d'une activite")
        display_toast(success=False, message="La suppression a échoué.")
        return return_good_display_activity(staff)


@admin_activity_bp.route('/admin/activity/delete/staff/<int:activity_id>', methods=['DELETE'])
@require_permission('activity')
def delete_activity_staff(activity_id, staff=None):
    return delete_activity(activity_id, staff=True)

# affiche le formulaire pour ajouter un activité
@admin_activity_bp.route('/admin/activity/add_form')
@require_permission('activity')
def add_activity_form():
    return render_template('/admin/activity_add_form.html',
                           schedules=_schedules_with_summary())


# affiche le formulaire pour ajouter un activité lié à un membre de l'équipe
@admin_activity_bp.route('/admin/activity/add_staff_form')
@require_permission('activity')
def add_activity_staff_form():
    return render_template('/admin/activity_add_form.html',
                            schedules=_schedules_with_summary(),
                            staff=Pharmacist.query.all())


# enregistre l'activité' dans la Bdd
@admin_activity_bp.route('/admin/activity/add_new_activity', methods=['POST'])
@require_permission('activity')
def add_new_activity():
    staff_id = request.form.get("staff_id")
    try:
        valeurs, erreur = _valider_activite(request.form)
        if erreur:
            # 204 : rien n'est remplacé, la saisie reste dans le formulaire.
            return display_toast(success=False, message=erreur)

        schedule_ids = valeurs["schedules"]
        staff_id = valeurs["staff_id"]

        # Point 6 : creation + rattachement des plages horaires dans UNE
        # transaction. Auparavant deux commits : un echec sur les plages laissait
        # une activite creee sans horaire, que le rollback ne pouvait plus annuler.
        with atomic():
            new_activity = Activity(
                name=valeurs["name"],
                letter=valeurs["letter"],
                inactivity_message=valeurs["inactivity_message"],
                specific_message=valeurs["specific_message"],
                notification=valeurs["notification"]
            )
            if staff_id:
                new_activity.is_staff = True
                new_activity.staff = Pharmacist.query.get(staff_id)

            db.session.add(new_activity)
            db.session.flush()

            for schedule_id in schedule_ids:
                schedule = ActivitySchedule.query.get(schedule_id)
                if schedule:
                    new_activity.schedules.append(schedule)

        # Même planification que la modification. L'ancien code créait ici
        # d'autres tâches (fonction + objet application en argument, non
        # sérialisables dans le jobstore SQLAlchemy) : l'activité était créée
        # mais la route répondait « L'ajout a échoué ».
        update_scheduler_for_activity(new_activity)

        # Effacer le formulaire via swap-oob (les deux conteneurs possibles).
        clear_form_html = ('<div hx-swap-oob="innerHTML:#div_add_activity_form"></div>'
                           '<div hx-swap-oob="innerHTML:#div_add_activity_form_staff"></div>')

        record_audit(ACTION_CREATE, "activity", target_id=new_activity.id,
                     outcome=OUTCOME_SUCCESS,
                     details=f"name={new_activity.name}")
        display_toast(success=True, message="Activité créée")
        return f"{return_good_display_activity(staff_id)}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_CREATE, "activity",
                     target_id=request.form.get('name'),
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de l'ajout d'une activite")
        return display_toast(success=False, message="L'ajout a échoué.")


def return_good_display_activity(staff):
    """ Sert uniquement à retourner le bon affichage entre activité et activité == équipier"""
    if staff:
        return display_activity_table_staff()
    else:
        return display_activity_table()


def _job_prefixes(name):
    return (f"disable_{name}_", f"enable_{name}_")


def remove_activity_jobs(*names):
    """Supprime les tâches d'ouverture/fermeture planifiées pour ces noms."""
    prefixes = tuple(p for name in names if name for p in _job_prefixes(name))
    if not prefixes:
        return
    try:
        for job in scheduler.get_jobs():
            if job.id.startswith(prefixes):
                scheduler.remove_job(job.id)
                app.logger.info(f"Removed existing job: {job.id}")
    except Exception as e:
        app.logger.error(f"Error removing existing jobs for {names}: {str(e)}")


def update_scheduler_for_activity(activity, previous_name=None):
    """(Re)planifie l'ouverture/fermeture des boutons de l'activité.

    ``previous_name`` : nom avant un renommage — ses tâches sont aussi
    retirées (les identifiants de tâche contiennent le nom).
    """
    # Constantes pour la configuration
    MISFIRE_GRACE_TIME = 300  # 5 minutes de délai de grâce

    job_id_disable_prefix, job_id_enable_prefix = _job_prefixes(activity.name)

    # Nettoyage des jobs existants
    remove_activity_jobs(activity.name, previous_name)

    def is_full_day(start_time, end_time):
        return start_time == time(0, 0) and end_time == time(23, 59)
    
    def is_full_week(weekdays):
        all_days = {'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'}
        active_days = {day.abbreviation.strip().lower() for day in weekdays}
        return active_days == all_days

    def add_job(job_id, func, args, trigger_args):
        try:
            scheduler.add_job(
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

