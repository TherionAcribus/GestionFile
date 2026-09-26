from flask import Blueprint, render_template, request, current_app as app
from sqlalchemy.orm import joinedload
from models import Counter, Activity, DashboardCard, Patient, db
from communication import communikation
from routes.admin_security import require_permission, require_permission_dashboard
from form_validation import Champ, LISTE_ENTIERS, extraire, valider
from transactions import atomic
from ui_feedback import display_toast
from audit_service import record_audit
from audit_log import (
    ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE,
    OUTCOME_FAILURE, OUTCOME_SUCCESS,
)

admin_counter_bp = Blueprint('admin_counter', __name__)

# Patients « au comptoir » : appelés ou en cours de service.
_BUSY_STATUSES = ('calling', 'ongoing')


def _current_patients():
    """``{counter_id: patient}`` pour les patients appelés / en cours."""
    patients = (Patient.query
                .filter(Patient.status.in_(_BUSY_STATUSES), Patient.counter_id.isnot(None))
                .order_by(Patient.timestamp)
                .all())
    return {p.counter_id: p for p in patients}


# page de base
@admin_counter_bp.route('/admin/counter')
@require_permission('counter')
def admin_counter():
    return render_template('/admin/counter.html',
                            counter_order = app.config['COUNTER_ORDER'])


# affiche la liste des comptoirs (cartes avec l'état en direct)
@admin_counter_bp.route('/admin/counter/table')
@require_permission('counter')
def display_counter_table():
    # Le gabarit lit counter.staff (+ ses activités) : chargement groupé.
    counters = (Counter.query
                .options(joinedload(Counter.staff))
                .order_by(Counter.sort_order, Counter.id)
                .all())
    current = _current_patients()
    items = [{"counter": c, "patient": current.get(c.id)} for c in counters]
    return render_template('admin/counter_htmx_table.html', items=items,
                           connected=sum(1 for c in counters if c.staff_id),
                           auto=sum(1 for c in counters if c.auto_calling))


#: Formulaire d'un comptoir (point 5 : schéma déclaratif).
#: « activities » (anciens « actes réalisés ») reste accepté pour la
#: compatibilité mais n'est plus proposé : il n'a jamais été utilisé pour
#: choisir les patients — ce sont les compétences de la personne connectée
#: qui décident (python.engine.algo_choice_next_patient).
SCHEMA_COMPTOIR = (
    Champ("name", obligatoire=True, libelle="Le nom", longueur_max=20),
    Champ("activities", type=LISTE_ENTIERS, libelle="Les activités"),
)


def _valider_comptoir(form):
    valeurs, erreurs = valider(
        extraire(SCHEMA_COMPTOIR, form.get, form.getlist), SCHEMA_COMPTOIR)
    return (None, erreurs[0]) if erreurs else (valeurs, None)


# mise à jour d'un comptoir (nom)
@admin_counter_bp.route('/admin/counter/counter_update/<int:counter_id>', methods=['POST'])
@require_permission('counter')
def update_counter(counter_id):
    counter = db.session.get(Counter, counter_id)
    if counter is None:
        return display_toast(success=False, message="Comptoir introuvable")

    valeurs, erreur = _valider_comptoir(request.form)
    if erreur:
        # 204 : la liste n'est pas remplacée, la saisie reste dans le formulaire.
        return display_toast(success=False, message=erreur)

    try:
        counter.name = valeurs["name"]
        # Ne touche aux anciennes « activités » que si elles sont envoyées :
        # le formulaire ne les propose plus, elles ne doivent pas être vidées.
        if "activities" in request.form:
            counter.activities = Activity.query.filter(
                Activity.id.in_(valeurs["activities"])).all()
        db.session.commit()
    except Exception:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "counter", target_id=counter_id,
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de la mise a jour d'un comptoir")
        return display_toast(success=False, message="La mise à jour a échoué.")

    record_audit(ACTION_UPDATE, "counter", target_id=counter_id,
                 outcome=OUTCOME_SUCCESS,
                 details=f"name={counter.name}")
    display_toast(success=True, message="Comptoir enregistré")
    # mise à jour liste des comptoirs
    communikation("admin", event="refresh_counter_order")
    return display_counter_table()


# déconnecte la personne d'un comptoir (oubli de déconnexion…)
@admin_counter_bp.route('/admin/counter/disconnect/<int:counter_id>', methods=['POST'])
@require_permission('counter')
def disconnect_counter(counter_id):
    counter = db.session.get(Counter, counter_id)
    if counter is None:
        return display_toast(success=False, message="Comptoir introuvable")
    name = counter.staff.name if counter.staff else None
    try:
        counter.staff = None
        # Sans personne, le comptoir ne peut plus servir l'appel automatique.
        counter.auto_calling = False
        db.session.commit()
    except Exception:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "counter", target_id=counter_id,
                     outcome=OUTCOME_FAILURE, details="déconnexion")
        app.logger.exception("Echec de la deconnexion d'un comptoir")
        return display_toast(success=False, message="La déconnexion a échoué.")
    record_audit(ACTION_UPDATE, "counter", target_id=counter_id,
                 outcome=OUTCOME_SUCCESS, details=f"déconnexion staff={name}")
    communikation("counter", event="update buttons")
    display_toast(success=True, message=(f"Session de {name} fermée sur le comptoir {counter.name}"
                                         if name else f"Comptoir {counter.name} libéré"))
    return display_counter_table()


# affiche la modale pour confirmer la suppression d'un comptoir
@admin_counter_bp.route('/admin/counter/confirm_delete/<int:counter_id>', methods=['GET'])
@require_permission('counter')
def confirm_delete_counter(counter_id):
    counter = Counter.query.get(counter_id)
    return render_template('/admin/counter_modal_confirm_delete.html', counter=counter,
                           patient=_current_patients().get(counter_id))


# supprime un comptoir
@admin_counter_bp.route('/admin/counter/delete/<int:counter_id>', methods=['DELETE'])
@require_permission('counter')
def delete_counter(counter_id):
    try:
        counter = Counter.query.get(counter_id)
        if not counter:
            display_toast(success=False, message="Comptoir introuvable")
            return display_counter_table()

        # Un patient appelé / en cours perdrait son comptoir (la relation
        # remet counter_id à vide) et resterait bloqué dans ce statut.
        patient = _current_patients().get(counter_id)
        if patient is not None:
            display_toast(success=False,
                          message=f"Le patient {patient.call_number} est en cours à ce comptoir : terminez-le avant de supprimer le comptoir.")
            return display_counter_table()

        had_staff = counter.staff_id is not None
        db.session.delete(counter)
        db.session.commit()

        record_audit(ACTION_DELETE, "counter", target_id=counter_id,
                     outcome=OUTCOME_SUCCESS)
        display_toast(success=True, message="Comptoir supprimé")
        communikation("admin", event="refresh_counter_order")
        if had_staff:
            communikation("counter", event="update buttons")

        return display_counter_table()

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_DELETE, "counter", target_id=counter_id,
                     outcome=OUTCOME_FAILURE)
        display_toast(success=False, message="La suppression a échoué.")
        app.logger.exception("Echec de la suppression d'un comptoir")
        return display_counter_table()


# affiche le formulaire pour ajouter un counter
@admin_counter_bp.route('/admin/counter/add_form')
@require_permission('counter')
def add_counter_form():
    return render_template('/admin/counter_add_form.html')


# enregistre le comptoir dans la Bdd
@admin_counter_bp.route('/admin/counter/add_new_counter', methods=['POST'])
@require_permission('counter')
def add_new_counter():
    try:
        valeurs, erreur = _valider_comptoir(request.form)
        if erreur:
            # 204 : rien n'est remplacé, la saisie reste dans le formulaire.
            return display_toast(success=False, message=erreur)

        name = valeurs["name"]
        activities_ids = valeurs["activities"]

        # Trouve l'ordre le plus élevé et ajoute 1, sinon commence à 0 si aucun bouton n'existe
        max_order_counter = Counter.query.order_by(Counter.sort_order.desc()).first()
        sort_order = (max_order_counter.sort_order or 0) + 1 if max_order_counter else 0

        # Point 6 : création + rattachement des activités dans UNE transaction.
        # `flush` attribue l'identifiant sans clore la transaction.
        with atomic():
            new_counter = Counter(
                name=name,
                sort_order=sort_order
            )
            db.session.add(new_counter)
            db.session.flush()

            for activity_id in activities_ids:
                activity = Activity.query.get(activity_id)
                if activity:
                    new_counter.activities.append(activity)

        record_audit(ACTION_CREATE, "counter", target_id=new_counter.id,
                     outcome=OUTCOME_SUCCESS, details=f"name={name}")
        display_toast(success=True, message="Comptoir ajouté")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_counter_form"></div>"""

        # mise à jour de la liste
        communikation("admin", event="refresh_counter_order")

        return f"{display_counter_table()}{clear_form_html}"


    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_CREATE, "counter", target_id=request.form.get('name'),
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de l'ajout d'un comptoir")
        return display_toast(success=False, message="L'ajout a échoué.")


@admin_counter_bp.route('/admin/counter/order_counter')
@require_permission('counter')
def order_counter_table():
    counters = Counter.query.order_by(Counter.sort_order).all()
    return render_template('admin/counter_order_counters.html', counters=counters)


@admin_counter_bp.route('/admin/counter/update_counter_order', methods=['POST'])
@require_permission('counter')
def update_counter_order():
    try:
        order_data = request.form.getlist('order[]')
        for index, counter_id in enumerate(order_data):
            # `Query.order_by(...).get()` : forme refusée par SQLAlchemy 2.
            counter = db.session.get(Counter, int(counter_id))
            if counter is not None:
                counter.sort_order = index
        db.session.commit()
        record_audit(ACTION_UPDATE, "counter", outcome=OUTCOME_SUCCESS,
                     details="réordonnancement")
        display_toast(success=True, message="Ordre mis à jour")
        # La liste principale suit le même ordre.
        return '', 200, {"HX-Trigger": "refresh_counter_table"}
    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "counter", outcome=OUTCOME_FAILURE,
                     details="réordonnancement")
        app.logger.exception("Echec du reordonnancement des comptoirs")
        # Auparavant aucun retour ici : la vue renvoyait None (erreur 500).
        return display_toast(success=False, message="La mise à jour a échoué.")

@admin_counter_bp.route('/admin/counter/dashboard')
@require_permission_dashboard('counter')
def dashboard_counter():
    # Le gabarit lit counter.staff.name par ligne : joinedload évite un N+1.
    counters = Counter.query.options(joinedload(Counter.staff)).all()

    dashboardcard = DashboardCard.query.filter_by(name="counter").first()

    return render_template('/admin/dashboard_counter.html', 
                            counters=counters,
                            dashboardcard=dashboardcard)



