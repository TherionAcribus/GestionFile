from flask import Blueprint, render_template, request, redirect, url_for, current_app as app
from models import Pharmacist, Activity, Counter, DashboardCard, db
from routes.admin_security import require_permission, require_permission_dashboard
from form_validation import Champ, LISTE_ENTIERS, extraire, valider
from transactions import atomic
from ui_feedback import display_toast
from communication import communikation
from audit_service import record_audit
from audit_log import (
    ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE,
    OUTCOME_FAILURE, OUTCOME_SUCCESS,
)
from staff_explain import (
    clean_language, competences, initials_taken, nominative_for,
    nominative_missing, normalize_initials,
)

admin_staff_bp = Blueprint('admin_staff', __name__)


def _activities_split():
    """``(ordinaires, nominatives)`` triées par lettre puis nom."""
    activities = Activity.query.order_by(Activity.letter, Activity.name).all()
    return ([a for a in activities if not a.is_staff],
            [a for a in activities if a.is_staff])


# base
@admin_staff_bp.route('/admin/staff')
@require_permission('staff')
def admin_staff():
    return render_template('/admin/staff.html')

# affiche la liste de l'équipe (cartes)
@admin_staff_bp.route('/admin/staff/table')
@require_permission('staff')
def display_staff_table():
    staff = Pharmacist.query.order_by(Pharmacist.name).all()
    ordinary, nominative = _activities_split()
    counters_by_staff = {}
    for counter in Counter.query.filter(Counter.staff_id.isnot(None)).order_by(Counter.sort_order).all():
        counters_by_staff.setdefault(counter.staff_id, []).append(counter)

    items = []
    for member in staff:
        ids = [a.id for a in member.activities]
        code, names = competences(ids, ordinary)
        items.append({
            "member": member,
            "counters": counters_by_staff.get(member.id, []),
            "competences": code,
            "competence_names": names,
            "language": clean_language(member.language),
            "nominative": [a.name for a in nominative_for(member.id, nominative)],
            "nominative_missing": nominative_missing(member.id, ids, nominative),
        })
    return render_template('admin/staff_htmx_table.html', items=items,
                           activities=ordinary, nominative=nominative,
                           connected=sum(1 for i in items if i["counters"]))


#: Formulaire d'un membre d'équipe — création ET modification (point 5).
SCHEMA_MEMBRE = (
    Champ("name", obligatoire=True, libelle="Le nom", longueur_max=50),
    Champ("initials", obligatoire=True, libelle="Les initiales", longueur_max=10),
    Champ("language", libelle="La langue", longueur_max=20),
    Champ("activities", type=LISTE_ENTIERS, libelle="Les activités"),
)


def _valider_membre(form, member_id=None):
    """``(valeurs, erreur)`` : schéma + initiales normalisées et uniques
    SANS tenir compte de la casse (la connexion au comptoir l'ignore)."""
    valeurs, erreurs = valider(
        extraire(SCHEMA_MEMBRE, form.get, form.getlist), SCHEMA_MEMBRE)
    if erreurs:
        return None, erreurs[0]
    valeurs["initials"] = normalize_initials(valeurs["initials"])
    others = Pharmacist.query.filter(Pharmacist.id != member_id).all() \
        if member_id is not None else Pharmacist.query.all()
    if initials_taken(valeurs["initials"], others):
        return None, "Ces initiales sont déjà utilisées par un autre membre."
    valeurs["language"] = clean_language(valeurs["language"])
    return valeurs, None


# mise à jour des informations d'un membre
@admin_staff_bp.route('/admin/staff/member_update/<int:member_id>', methods=['POST'])
@require_permission('staff')
def update_member(member_id):
    member = db.session.get(Pharmacist, member_id)
    if member is None:
        return display_toast(success=False, message="Membre de l'équipe introuvable")

    valeurs, erreur = _valider_membre(request.form, member_id)
    if erreur:
        # 204 : la liste n'est pas remplacée, la saisie reste dans le formulaire.
        return display_toast(success=False, message=erreur)

    try:
        member.name = valeurs["name"]
        member.initials = valeurs["initials"]
        member.language = valeurs["language"]
        member.activities = Activity.query.filter(
            Activity.id.in_(valeurs["activities"])).all()
        db.session.commit()
    except Exception:
        # Rollback avant l'audit : le commit interne de record_audit ne doit
        # pas persister de mutations métier restées en attente.
        db.session.rollback()
        record_audit(ACTION_UPDATE, "staff", target_id=member_id,
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de la mise a jour d'un membre")
        return display_toast(success=False, message="La mise à jour a échoué.")

    record_audit(ACTION_UPDATE, "staff", target_id=member_id,
                 outcome=OUTCOME_SUCCESS,
                 details=f"name={member.name}")
    # Compétences modifiées : les comptoirs rechargent leurs boutons.
    communikation("counter", event="update buttons")
    display_toast(success=True, message="Membre enregistré")
    return display_staff_table()


# affiche la modale pour confirmer la suppression d'un membre
@admin_staff_bp.route('/admin/staff/confirm_delete/<int:member_id>', methods=['GET'])
@require_permission('staff')
def confirm_delete(member_id):
    staff = Pharmacist.query.get(member_id)
    return render_template('/admin/staff_modal_confirm_delete.html', staff=staff,
                           counters=Counter.query.filter_by(staff_id=member_id).all(),
                           nominative=Activity.query.filter_by(staff_id=member_id).all())


# supprime un membre de l'equipe
@admin_staff_bp.route('/admin/staff/delete/<int:member_id>', methods=['DELETE'])
@require_permission('staff')
def delete_staff(member_id):
    try:
        member = Pharmacist.query.get(member_id)
        if not member:
            display_toast(success=False, message="Membre de l'équipe non trouvé")
            return display_staff_table()

        was_connected = Counter.query.filter_by(staff_id=member_id).count() > 0
        # Les relations ORM (Counter.staff, Activity.staff) remettent à vide
        # le comptoir et les demandes nominatives de ce membre.
        db.session.delete(member)
        db.session.commit()
        record_audit(ACTION_DELETE, "staff", target_id=member_id,
                     outcome=OUTCOME_SUCCESS)
        if was_connected:
            # Le comptoir qu'il occupait se retrouve sans personne.
            communikation("counter", event="update buttons")
        display_toast(success=True, message="Membre supprimé")
        return display_staff_table()

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_DELETE, "staff", target_id=member_id,
                     outcome=OUTCOME_FAILURE)
        display_toast(success=False, message="La suppression a échoué.")
        app.logger.exception("Echec de la suppression d'un membre")
        return display_staff_table()


# affiche le formulaire pour ajouter un membre
@admin_staff_bp.route('/admin/staff/add_form')
@require_permission('staff')
def add_staff_form():
    ordinary, nominative = _activities_split()
    return render_template('/admin/staff_add_form.html', activities=ordinary,
                           nominative=nominative)


# enregistre le membre dans la Bdd
@admin_staff_bp.route('/admin/staff/add_new_staff', methods=['POST'])
@require_permission('staff')
def add_new_staff():
    try:
        valeurs, erreur = _valider_membre(request.form)
        if erreur:
            # 204 : rien n'est remplacé, la saisie reste dans le formulaire.
            return display_toast(success=False, message=erreur)

        # Point 6 : création + rattachement des activités dans UNE transaction.
        with atomic():
            new_staff = Pharmacist(
                name=valeurs["name"],
                initials=valeurs["initials"],
                language=valeurs["language"],
            )
            db.session.add(new_staff)
            db.session.flush()

            for activity_id in valeurs["activities"]:
                activity = Activity.query.get(activity_id)
                if activity:
                    new_staff.activities.append(activity)

        display_toast(success=True, message="Membre ajouté")
        record_audit(ACTION_CREATE, "staff", target_id=new_staff.id,
                     outcome=OUTCOME_SUCCESS,
                     details=f"name={new_staff.name}")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_staff_form"></div>"""

        return f"{display_staff_table()}{clear_form_html}"

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_CREATE, "staff", target_id=request.form.get('name'),
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de l'ajout d'un membre")
        return display_toast(success=False, message="L'ajout a échoué.")


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
        record_audit(ACTION_CREATE, "counter", target_id=new_counter.id,
                     outcome=OUTCOME_SUCCESS, details=f"name={name}")
        return redirect('/admin')
    return "Erreur dans la soumission du formulaire"



# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @admin_staff_bp.route('/update_pharmacist/<int:pharmacist_id>', methods=['POST'])
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
        record_audit(ACTION_UPDATE, "staff", target_id=pharmacist_id,
                     outcome=OUTCOME_SUCCESS,
                     details=f"name={pharmacist.name}")
    # ATTENTION si cette route est reactivee : 'admin_staff.pharmacists' n'existe
    # plus (elle rendait pharmacists.html, fichier inexistant -> 500, et a ete
    # retiree). Ce url_for leverait donc un BuildError. Viser /admin/staff, qui
    # est l'interface vivante du personnel.
    return redirect(url_for('admin_staff.pharmacists'))



# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @admin_staff_bp.route('/add_pharmacist', methods=['POST'])
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
    record_audit(ACTION_CREATE, "staff", target_id=new_pharmacist.id,
                 outcome=OUTCOME_SUCCESS, details=f"name={name}")
    return render_template('htmx/menu_admin_pharmacist_row.html', pharmacist=new_pharmacist)



# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @admin_staff_bp.route('/new_pharmacist_form')
@require_permission('staff')
def new_pharmacist_form():
    app.logger.debug("new_pharmacist_form")
    return render_template('htmx/menu_admin_new_pharmacist_form.html')
