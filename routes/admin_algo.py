import json
from datetime import datetime

from flask import Blueprint, render_template, request, current_app as app
from models import AlgoRule, Activity, ConfigOption, Patient, db, bump_queue_revision, DAY_ABBREVIATIONS, DAY_NAMES_FR
from algo_explain import STATE_ACTIVE, rule_state, summarize_rule
from config import time_tz
from routes.admin_security import require_permission
from ui_feedback import display_toast
from audit_service import record_audit
from audit_log import (
    ACTION_CREATE, ACTION_DELETE, ACTION_UPDATE,
    OUTCOME_FAILURE, OUTCOME_SUCCESS,
)
from form_validation import Champ, ENTIER, TEXTE, extraire, valider
from utils import parse_time

admin_algo_bp = Blueprint('admin_algo', __name__)

# Toute modification de règle rafraîchit le bloc d'état en haut de page
# (hx-trigger « algoChanged from:body »). Fusionné avec l'en-tête
# adminFeedback par app._attach_admin_feedback.
_ALGO_CHANGED = {"HX-Trigger": json.dumps({"algoChanged": True})}


def _overtaken_limit():
    try:
        return int(app.config['ALGO_OVERTAKEN_LIMIT'])
    except (TypeError, ValueError):
        return 0


def _now():
    """Instant dans le fuseau de l'application, comme le moteur."""
    return datetime.now(time_tz)


def _rules_with_state(now=None, waiting=None):
    """Règles triées (niveau, début, nom) avec résumé et état à l'instant."""
    now = now or _now()
    if waiting is None:
        waiting = Patient.query.filter_by(status='standing').count()
    now_time = now.time()
    day_abbr = DAY_ABBREVIATIONS[now.weekday()]
    rules = AlgoRule.query.order_by(
        AlgoRule.priority_level, AlgoRule.start_time, AlgoRule.name).all()
    return [
        {
            "rule": rule,
            "summary": summarize_rule(rule),
            "state": rule_state(rule, now_time=now_time, day_abbr=day_abbr,
                                waiting=waiting),
        }
        for rule in rules
    ]

# Schéma commun création / édition : les conversions numériques sont faites
# par `valider` (l'ancien code comparait des chaînes : "9" > "10" était vrai).
SCHEMA_REGLE = (
    Champ("name", obligatoire=True, libelle="Le nom", longueur_max=100),
    Champ("activity_id", type=ENTIER, obligatoire=True, libelle="L'activité"),
    Champ("priority_level", type=ENTIER, obligatoire=True, libelle="Le niveau de priorité",
          choix=(1, 2, 3, 4, 5)),
    Champ("min_patients", type=ENTIER, obligatoire=True, libelle="Le minimum de patients"),
    Champ("max_patients", type=ENTIER, obligatoire=True, libelle="Le maximum de patients"),
    Champ("max_overtaken", type=ENTIER, obligatoire=True, libelle="Le dépassement maximum"),
    Champ("start_time", type=TEXTE, obligatoire=True, libelle="L'heure de début"),
    Champ("end_time", type=TEXTE, obligatoire=True, libelle="L'heure de fin"),
)


def valider_regle_algo(form):
    """Validation croisée d'une règle : conversions, bornes, jours, activité.

    Renvoie ``(valeurs, erreur)`` — ``valeurs`` prêt pour le modèle si
    ``erreur`` est None. Les contrôles reflètent les CHECK de la table (le
    serveur reste le dernier rempart même sans contrainte SQL sur une vieille
    base).
    """
    valeurs, erreurs = valider(
        extraire(SCHEMA_REGLE, form.get, form.getlist), SCHEMA_REGLE)
    if erreurs:
        return None, erreurs[0]

    if valeurs["min_patients"] < 0 or valeurs["max_overtaken"] < 0:
        return None, "Les nombres de patients ne peuvent pas être négatifs."
    if valeurs["min_patients"] > valeurs["max_patients"]:
        return None, "Le nombre de patients maximum doit être supérieur au nombre de patients minimum"

    start_time = parse_time(valeurs["start_time"])
    end_time = parse_time(valeurs["end_time"])
    if start_time is None or end_time is None:
        return None, "Format d'heure invalide (HH:MM attendu)."
    if start_time >= end_time:
        return None, "L'heure de fin doit être après l'heure de début."
    valeurs["start_time"] = start_time
    valeurs["end_time"] = end_time

    # Jours : liste d'abréviations anglaises ('Mon,Tue,...'). Champ absent ou
    # vide -> tous les jours, comme le défaut historique du formulaire.
    days_raw = form.getlist("days_of_week")
    days = [d.strip() for d in days_raw if d and d.strip()]
    # Le formulaire (cases à cocher) signale sa présence : tout décocher ne
    # doit pas silencieusement signifier « tous les jours ».
    if not days and form.get("days_of_week_present"):
        return None, "Cochez au moins un jour d'application."
    if days:
        invalides = [d for d in days if d not in DAY_ABBREVIATIONS]
        if invalides:
            return None, "Jour de la semaine invalide."
        valeurs["days_of_week"] = ",".join(dict.fromkeys(days))
    else:
        valeurs["days_of_week"] = ",".join(DAY_ABBREVIATIONS)

    if not Activity.query.get(valeurs["activity_id"]):
        return None, "Activité introuvable."

    return valeurs, None

# page de base
@admin_algo_bp.route('/admin/algo')
@require_permission('algo')
def admin_algo():
    return render_template('/admin/algo.html',
                            algo_overtaken_limit=app.config['ALGO_OVERTAKEN_LIMIT'])

@admin_algo_bp.route('/admin/algo/table')
@require_permission('algo')
def display_algo_table():
    activities = Activity.query.all()
    return render_template('admin/algo_htmx_table.html',
                           rules=_rules_with_state(),
                           activities=activities, days=DAY_NAMES_FR.items())


def _render_status():
    """Bloc d'état : interrupteur + ce que fait l'algorithme en ce moment."""
    limit = _overtaken_limit()
    waiting = Patient.query.filter_by(status='standing').count()
    rules = _rules_with_state(waiting=waiting)
    active_rules = [r["rule"] for r in rules if r["state"][0] == STATE_ACTIVE]
    # Même frein global que algo_choice_next_patient : un patient dépassé
    # au moins `limit` fois suspend l'algorithme.
    blocked_patient = (Patient.query
                       .filter(Patient.status == 'standing',
                               Patient.overtaken >= limit)
                       .order_by(Patient.overtaken.desc())
                       .first())
    return render_template("admin/algo_des_activate_buttons.html",
                           algo_activated=app.config['ALGO_IS_ACTIVATED'],
                           rules_count=len(rules),
                           active_rules=active_rules,
                           waiting=waiting,
                           blocked_patient=blocked_patient,
                           overtaken_limit=limit)


# bloc d'état + interrupteur d'activation
@admin_algo_bp.route('/admin/button_des_activate_algo')
@require_permission('algo')
def button_des_activate_algo():
    return _render_status()

# active ou desactive l'algorithme, enregistre l'info, retourne les boutons
@admin_algo_bp.route('/admin/algo/toggle_activation', methods=['POST'])
@require_permission('algo')
def toggle_activation():
    action = request.args.get('action', 'activate')
    is_activated = action == 'activate'
    
    app.config['ALGO_IS_ACTIVATED'] = is_activated
    algo_activated = ConfigOption.query.filter_by(config_key="algo_activate").first()
    algo_activated.value_bool = is_activated
    db.session.commit()
    bump_queue_revision()  # l'activation change l'ordre affiché : invalide le cache
    record_audit(ACTION_UPDATE, "config", target_id="algo_activate",
                 outcome=OUTCOME_SUCCESS, details=f"value={is_activated}")

    return _render_status()


# [PT3] Route desactivee le 2026-09-05 : aucune reference dans le depot
# (gabarits, JS, App_Comptoir, borne). Reactiver = decommenter la ligne
# ci-dessous, puis retirer l'entree de ROUTES_DESACTIVEES dans
# tests/test_code_mort.py.
# @admin_algo_bp.route('/admin/algo/change_overtaken_limit', methods=['POST'])
@require_permission('algo')
def change_overtaken_limit():
    overtaken_limit = request.form.get('overtaken_limit')

    app.config['ALGO_OVERTAKEN_LIMIT'] = overtaken_limit
    try:
        algo_overtaken_limit = ConfigOption.query.filter_by(config_key="algo_overtaken_limit").first()
        algo_overtaken_limit.value_int = overtaken_limit
        db.session.commit()
        record_audit(ACTION_UPDATE, "config", target_id="algo_overtaken_limit",
                     outcome=OUTCOME_SUCCESS, details=f"value={overtaken_limit}")
        return display_toast()
    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "config", target_id="algo_overtaken_limit",
                     outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de l'enregistrement de la limite de depassement")
        return display_toast(success=False, message="La mise à jour a échoué.")


# affiche le formulaire pour ajouter une regle de l'algo
@admin_algo_bp.route('/admin/algo/add_rule_form')
@require_permission('algo')
def add_rule_form():
    activities = Activity.query.all()
    return render_template('/admin/algo_add_rule_form.html', activities=activities,
                           days=DAY_NAMES_FR.items())


# enregistre la regledans la Bdd
@admin_algo_bp.route('/admin/algo/add_new_rule', methods=['POST'])
@require_permission('algo')
def add_new_rule():
    try:
        valeurs, erreur = valider_regle_algo(request.form)
        if erreur:
            # 204 : rien n'est remplacé, la saisie reste dans le formulaire.
            return display_toast(success=False, message=erreur)

        new_rule = AlgoRule(**valeurs)
        db.session.add(new_rule)
        db.session.commit()
        # L'ordre affiché dépend des règles : invalide le cache « prochains
        # patients » immédiatement plutôt que d'attendre la prochaine mutation.
        bump_queue_revision()

        record_audit(ACTION_CREATE, "algo_rule", target_id=new_rule.id,
                     outcome=OUTCOME_SUCCESS, details=f"name={new_rule.name}")
        display_toast(success=True, message="Règle ajoutée avec succès")

        # Effacer le formulaire via swap-oob
        clear_form_html = """<div hx-swap-oob="innerHTML:#div_add_rule_form"></div>"""

        return f"{display_algo_table()}{clear_form_html}", 200, _ALGO_CHANGED

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_CREATE, "algo_rule",
                     target_id=request.form.get('name'), outcome=OUTCOME_FAILURE)
        app.logger.exception("Echec de l'ajout d'une regle d'algorithme")
        return display_toast(success=False, message="L'ajout a échoué.")


# affiche la modale pour confirmer la suppression d'un membre
@admin_algo_bp.route('/admin/algo/confirm_delete_rule/<int:rule_id>', methods=['GET'])
@require_permission('algo')
def confirm_delete_rule(rule_id):
    rule = AlgoRule.query.get(rule_id)
    return render_template('/admin/algo_modal_confirm_delete_rule.html', rule=rule)


# supprime une regle de l'algo
@admin_algo_bp.route('/admin/algo/delete_rule/<int:algo_id>', methods=['DELETE'])
@require_permission('algo')
def delete_algo(algo_id):
    try:
        rule = AlgoRule.query.get(algo_id)
        if not rule:
            display_toast(success=False, message="Règle non trouvée")
            return display_algo_table()

        db.session.delete(rule)
        db.session.commit()
        bump_queue_revision()  # invalide le cache « prochains patients »

        record_audit(ACTION_DELETE, "algo_rule", target_id=algo_id,
                     outcome=OUTCOME_SUCCESS)
        display_toast(success=True, message="Règle supprimée")
        return display_algo_table(), 200, _ALGO_CHANGED

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_DELETE, "algo_rule", target_id=algo_id,
                     outcome=OUTCOME_FAILURE)
        display_toast(success=False, message="La suppression a échoué.")
        return display_algo_table()


@admin_algo_bp.route('/admin/algo/rule_update/<int:rule_id>', methods=['POST'])
@require_permission('algo')
def update_algo_rule(rule_id):
    try:
        rule = AlgoRule.query.get(rule_id)
        if rule:
            valeurs, erreur = valider_regle_algo(request.form)
            if erreur:
                # 204 : la liste n'est pas remplacée, la saisie est conservée.
                return display_toast(success=False, message=erreur)

            rule.name = valeurs["name"]
            rule.activity_id = valeurs["activity_id"]
            rule.priority_level = valeurs["priority_level"]
            rule.min_patients = valeurs["min_patients"]
            rule.max_patients = valeurs["max_patients"]
            rule.max_overtaken = valeurs["max_overtaken"]
            rule.start_time = valeurs["start_time"]
            rule.end_time = valeurs["end_time"]
            rule.days_of_week = valeurs["days_of_week"]

            db.session.commit()
            # Les règles modifiées changent l'ordre affiché : invalide le cache.
            bump_queue_revision()
            record_audit(ACTION_UPDATE, "algo_rule", target_id=rule_id,
                         outcome=OUTCOME_SUCCESS, details=f"name={rule.name}")

            display_toast(success=True, message="Règle enregistrée")
            # Liste re-rendue : résumé et état « en vigueur » à jour.
            return display_algo_table(), 200, _ALGO_CHANGED
        else:
            return display_toast(success=False, message="Règle introuvable")

    except Exception as e:
            db.session.rollback()
            record_audit(ACTION_UPDATE, "algo_rule", target_id=rule_id,
                         outcome=OUTCOME_FAILURE)
            app.logger.exception("Echec de la mise a jour d'une regle d'algorithme")
            return display_toast(success=False, message="La mise à jour a échoué.")