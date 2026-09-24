"""Routes de modification de la configuration depuis l'administration.

Extrait d'``app.py`` (point 9.5d) : les interrupteurs, champs de saisie, listes
déroulantes et variables CSS de l'interface d'administration, plus les pages
« base de données » et « tâches planifiées ».

Chaque écriture passe par ``authorize_config_change`` puis par le registre des
paramètres (``params_registry``), qui associe à chaque clé une permission et un
validateur — c'est lui, et non la vue, qui décide de ce qui est modifiable.
"""

import json
import os
from pathlib import Path

from flask import Blueprint, current_app as app, jsonify, render_template, request

import config_sync
from communication import communikation
from extensions import scheduler
from flask_security import current_user
from models import db, ConfigOption, DashboardCard, JobExecutionLog
from params_registry import get_spec, column_values_for
from routes.admin_security import (
    permission_error_response,
    user_has_permission,
    require_permission,
    require_permission_dashboard,
)
from scheduler_dashboard import build_jobs_info
from scheduler_functions import (
    add_scheduler_clear_all_patients,
    remove_scheduler_clear_all_patients,
    remove_scheduler_clear_announce_calls,
    scheduler_clear_announce_calls,
)
from ui_feedback import display_toast
from utils import validate_config_text
from variables import is_safe_css_value, is_valid_css_variable_name
from path_security import UnsafePathError, safe_path_under, to_abs_base_dir, validate_path_segment
from upload_security import ALLOWED_AUDIO_EXTENSIONS
from audit_service import record_audit
from audit_log import ACTION_UPDATE, OUTCOME_FAILURE, OUTCOME_SUCCESS

admin_config_bp = Blueprint('admin_config', __name__)

#: Permission requise selon la page ciblée par une variable CSS.
CSS_SOURCE_PERMISSION = {
    'patient': 'patient',
    'announce': 'announce',
    'phone': 'phone',
}

def authorize_config_change(key, expected_value_type=None):
    """Contrôle d'accès commun aux routes de modification des paramètres.

    Retourne ``(spec, None)`` si la modification est autorisée, sinon
    ``(None, (réponse, statut))`` à renvoyer tel quel :

    - **401** si l'utilisateur n'est pas authentifié ;
    - **400** si la clé est absente du registre (``PARAM_REGISTRY``) ou d'un
      type incompatible avec la route appelée ;
    - **403** si l'utilisateur n'a pas la permission associée à la clé.

    Aucune confiance n'est accordée aux données du navigateur : la permission
    et le type proviennent exclusivement du registre serveur.
    """
    if not current_user.is_authenticated:
        app.logger.warning("Modification de paramètre refusée (non authentifié) : %r", key)
        return None, (jsonify({"error": "Unauthorized"}), 401)

    spec = get_spec(key)
    if spec is None:
        app.logger.warning("Modification de paramètre refusée (clé inconnue) : %r", key)
        return None, (jsonify({"error": "Unknown parameter"}), 400)

    if expected_value_type is not None and spec.value_type != expected_value_type:
        app.logger.warning(
            "Modification de paramètre refusée (type %s attendu pour %s, registre=%s)",
            expected_value_type, key, spec.value_type)
        return None, (jsonify({"error": "Invalid parameter type"}), 400)

    if not user_has_permission(current_user, spec.permission):
        app.logger.warning(
            "Modification de '%s' refusée à %s (permission '%s' requise)",
            key, getattr(current_user, "username", "?"), spec.permission)
        return None, (jsonify({"error": "Forbidden"}), 403)

    return spec, None


def _admin_theme_names():
    """Noms des thèmes admin réellement disponibles (static/css/themes/*.css).

    ``admin_colors`` devient le nom du fichier de thème chargé par
    ``admin/base.html`` : la valeur doit correspondre à un fichier existant,
    pas à une chaîne arbitraire forgée côté client.
    """
    themes_path = os.path.join(app.static_folder, 'css', 'themes')
    try:
        return {os.path.splitext(name)[0]
                for name in os.listdir(themes_path) if name.endswith('.css')}
    except OSError:
        return set()


def _signal_file_exists(filename) -> bool:
    """``True`` si ``filename`` est un fichier audio réel du dossier signaux.

    Même contrôle que ``select_signal`` (admin_announce) : extension en liste
    blanche, segment de chemin sain, fichier existant. Sans lui, la clé
    ``announce_alert_filename`` restait inscriptible en chaîne arbitraire via
    ``update_select``/``update_input``.
    """
    if (not isinstance(filename, str) or '.' not in filename
            or filename.rsplit('.', 1)[1].lower() not in ALLOWED_AUDIO_EXTENSIONS):
        return False
    try:
        validate_path_segment(filename, what="sound filename")
        signals_dir = to_abs_base_dir(
            Path("static") / "audio" / "signals", root_dir=app.root_path)
        return safe_path_under(signals_dir, filename).is_file()
    except UnsafePathError:
        return False


def _closed_value_allowed(spec, value) -> bool:
    """Validation de domaine des paramètres à choix fermé.

    ``spec.allowed_values`` couvre les listes déroulantes statiques ; les
    validateurs ``theme``/``sound_file`` couvrent les choix dérivés de
    ressources serveur (fichiers de thème, fichiers audio). Sans ces gardes,
    ``update_select`` persistait n'importe quelle chaîne envoyée par le
    navigateur.
    """
    if spec.allowed_values is not None and value not in spec.allowed_values:
        return False
    if spec.validator == "theme" and value not in _admin_theme_names():
        return False
    if spec.validator == "sound_file" and not _signal_file_exists(value):
        return False
    return True


@admin_config_bp.route('/admin/update_switch', methods=['POST'])
def update_switch():
    """ Mise à jour des switches d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')

    spec, error = authorize_config_change(key, expected_value_type="value_bool")
    if error:
        return error

    bool_value = value == "true"
    try:
        # Mutation en base dans une seule transaction. On ne touche PAS à
        # app.config ici : la mémoire ne doit refléter le changement qu'APRÈS un
        # commit réussi (point 10), pour ne pas diverger de la base si le commit
        # échoue.
        config_option = ConfigOption.query.filter_by(config_key=key).first()
        if config_option:
            config_option.value_bool = bool_value
        else:
            config_option = ConfigOption(config_key=key, value_bool=bool_value)
            db.session.add(config_option)

        # Point 11 : incrémenter la génération de configuration DANS la même
        # transaction, pour que les autres processus (répliques web, scheduler)
        # rechargent app.config. On ne le fait PAS pour les paramètres nécessitant
        # un redémarrage : ils ne s'appliquent qu'à l'initialisation du processus.
        if not spec.restart_required:
            config_sync.bump_generation()

        db.session.commit()
        record_audit(ACTION_UPDATE, "config", target_id=key,
                     outcome=OUTCOME_SUCCESS, details=f"value={bool_value}")
    except Exception as e:
        # Toute exception annule la transaction (rollback) : la base reste dans
        # son état précédent et app.config n'a pas été modifié.
        db.session.rollback()
        record_audit(ACTION_UPDATE, "config", target_id=key,
                     outcome=OUTCOME_FAILURE)
        app.logger.error("Échec de mise à jour du switch %r : %s", key, e)
        return display_toast(success=False, message="La mise à jour a échoué.")

    # Paramètre nécessitant un redémarrage : la valeur est persistée mais n'est
    # PAS appliquée à chaud (ni ici ni sur les autres processus). On ne mute donc
    # pas app.config et on l'annonce clairement plutôt que de prétendre l'inverse.
    if spec.restart_required:
        return display_toast(success=True, message=config_sync.RESTART_REQUIRED_MESSAGE)

    # Commit réussi : refléter en mémoire, puis déclencher les effets de bord.
    app.config[spec.config_name] = bool_value
    call_function_with_switch(key, value)
    return display_toast(success=True, message="Option mise à jour.")
    


# Chaque « source » de variables CSS correspond à une page d'administration : la
# permission requise pour modifier son apparence est donc celle de cette page.
# Source unique de vérité pour les routes CSS génériques ci-dessous, dont la
# permission dépend de données de la requête (et ne peut donc pas être fixée par
# un décorateur statique).
@admin_config_bp.route('/admin/update_css_variable', methods=['POST'])
def update_css_variable():
    app.logger.debug("UPDATE!!!")

    source_name = request.form.get('source')
    variable_name = request.form.get('variable')
    value = request.form.get('value')

    # Point 3 (audit Admin) : json.loads peut lever JSONDecodeError si le
    # client envoie un JSON mal formé. On valide AVANT toute mutation.
    raw_dependencies = request.form.get('dependencies', '[]')
    try:
        dependencies = json.loads(raw_dependencies)
    except (json.JSONDecodeError, TypeError):
        app.logger.warning(
            "update_css_variable : dependencies invalide reçu de %s",
            getattr(current_user, "username", "?"),
        )
        return jsonify({'status': 'error', 'message': 'Données de dépendances invalides.'}), 400

    if not isinstance(dependencies, list):
        return jsonify({'status': 'error', 'message': 'Les dépendances doivent être une liste.'}), 400

    # Permission liée à la page ciblée par la variable CSS.
    resource = CSS_SOURCE_PERMISSION.get(source_name)
    if resource is None:
        return jsonify({'status': 'error', 'message': 'Source invalide'}), 400
    refusal = permission_error_response(resource, api=True)
    if refusal is not None:
        return refusal

    # Nom de variable : identifiant CSS strict, et seules les variables
    # EXISTANTES sont modifiables — sinon ``update_variable`` créerait une
    # entrée arbitraire ensuite écrite dans la feuille personnalisée.
    if not is_valid_css_variable_name(variable_name):
        return jsonify({'status': 'error', 'message': 'Nom de variable invalide.'}), 400
    known_variables = app.css_variable_manager.get_all_variables(source_name)
    if variable_name not in known_variables:
        return jsonify({'status': 'error', 'message': 'Variable inconnue.'}), 400
    for dep_variable in dependencies:
        if not is_valid_css_variable_name(dep_variable) or dep_variable not in known_variables:
            return jsonify({'status': 'error', 'message': 'Variable dépendante invalide.'}), 400

    # La valeur est recopiée telle quelle dans ``--nom: valeur;`` : elle doit
    # rester une valeur CSS inerte (couleur, taille…) — jamais une séquence
    # capable de sortir de la déclaration ou de charger une ressource.
    if not is_safe_css_value(value):
        return jsonify({'status': 'error', 'message': 'Valeur CSS non autorisée.'}), 400

    try:
        # Met à jour la variable dans la base de données
        app.css_variable_manager.update_variable(source_name, variable_name, value)

        # Met à jour toutes les variables dépendantes
        for dep_variable in dependencies:
            app.css_variable_manager.update_variable(
                source_name,
                dep_variable,
                value
            )

        # Récupère toutes les variables pour générer le CSS
        variables = app.css_variable_manager.get_all_variables(source_name)

        # Génère le nouveau CSS
        new_css_url = app.css_manager.generate_css(variables, mode=source_name)
    except Exception as e:
        # Rollback avant l'audit : css_variable_manager partage db.session et
        # peut laisser des mutations partielles en attente.
        db.session.rollback()
        record_audit(ACTION_UPDATE, "css_variable",
                     target_id=f"{source_name}.{variable_name}",
                     outcome=OUTCOME_FAILURE)
        app.logger.error("Échec update_css_variable (%s/%s) : %s", source_name, variable_name, e)
        return jsonify({'status': 'error', 'message': 'La mise à jour de la variable a échoué.'}), 500

    record_audit(ACTION_UPDATE, "css_variable",
                 target_id=f"{source_name}.{variable_name}",
                 outcome=OUTCOME_SUCCESS, details=f"value={value}")
    return jsonify({
        'status': 'success',
        'css_url': new_css_url
    })



@admin_config_bp.route('/admin/copy_colors', methods=['POST'])
def copy_colors():
    """Copie les couleurs parentes d'une page source vers une page cible"""
    try:
        # Point 3 (audit Admin) : request.get_json() peut retourner None si
        # le Content-Type n'est pas JSON. On valide avant d'accéder aux clés.
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({'status': 'error', 'message': 'Corps JSON manquant ou invalide.'}), 400

        source_page = data.get('source_page')
        target_page = data.get('target_page')
        mappings = data.get('mappings', [])

        if not all([source_page, target_page, mappings]):
            return jsonify({'status': 'error', 'message': 'Données manquantes'}), 400

        if not isinstance(mappings, list):
            return jsonify({'status': 'error', 'message': 'Les mappings doivent être une liste.'}), 400

        # Permission : l'écriture porte sur chaque page cible ; l'utilisateur doit
        # avoir la permission de modifier toutes les pages ciblées.
        for target_source in {m.get('target_source') for m in mappings}:
            resource = CSS_SOURCE_PERMISSION.get(target_source)
            if resource is None:
                return jsonify({'status': 'error', 'message': 'Source cible invalide'}), 400
            refusal = permission_error_response(resource, api=True)
            if refusal is not None:
                return refusal

        # Validation AVANT toute écriture : les noms de variables cibles et de
        # dépendances doivent être des identifiants stricts correspondant à des
        # variables existantes (sinon des entrées arbitraires — voire des
        # séquences d'injection CSS — seraient créées puis écrites dans la
        # feuille personnalisée). La source doit elle aussi être connue.
        for mapping in mappings:
            source_source = mapping.get('source_source')
            target_source = mapping.get('target_source')
            if source_source not in CSS_SOURCE_PERMISSION:
                return jsonify({'status': 'error', 'message': 'Source invalide'}), 400
            target_vars = app.css_variable_manager.get_all_variables(target_source)
            names = [mapping.get('target_var'), *mapping.get('dependencies', [])]
            for name in names:
                if not is_valid_css_variable_name(name) or name not in target_vars:
                    return jsonify({'status': 'error', 'message': 'Variable cible invalide.'}), 400

        # Pour chaque mapping, lire la valeur source et l'écrire dans la cible
        for mapping in mappings:
            source_var = mapping.get('source_var')
            target_var = mapping.get('target_var')
            source_source = mapping.get('source_source')  # ex: 'patient', 'announce', 'phone'
            target_source = mapping.get('target_source')

            value = app.css_variable_manager.get_variable(source_source, source_var)
            if value:
                # Met à jour la variable parente cible
                app.css_variable_manager.update_variable(target_source, target_var, value)

                # Met à jour aussi les variables dépendantes via colorMappings (côté client)
                dep_variables = mapping.get('dependencies', [])
                for dep_var in dep_variables:
                    app.css_variable_manager.update_variable(target_source, dep_var, value)

        # Régénère le CSS pour la/les source(s) cible(s)
        target_sources = set(m.get('target_source') for m in mappings)
        for ts in target_sources:
            variables = app.css_variable_manager.get_all_variables(ts)
            app.css_manager.generate_css(variables, mode=ts)

        record_audit(ACTION_UPDATE, "css_variable", target_id=target_page,
                     outcome=OUTCOME_SUCCESS,
                     details=f"copie depuis {source_page}")
        return jsonify({'status': 'success', 'message': 'Couleurs copiées avec succès'})

    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "css_variable",
                     outcome=OUTCOME_FAILURE, details="copie de couleurs")
        # Point 3 : ne pas renvoyer str(e) au client (fuite d'information
        # technique). Journaliser le détail côté serveur.
        app.logger.error("Échec copy_colors : %s", e)
        return jsonify({'status': 'error', 'message': 'La copie des couleurs a échoué.'}), 500



@admin_config_bp.route('/admin/update_input', methods=['POST'])
def update_input():
    """ Mise à jour des input d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')

    spec, error = authorize_config_change(key)
    if error:
        return error

    # Le type de validation vient du registre serveur, jamais du paramètre
    # ``check`` envoyé par le navigateur.
    validator = spec.validator
    if value is None:
        value = ""

    # Secrets (mot de passe SMTP, clé Spotify...) : le formulaire n'affiche
    # jamais la valeur courante et envoie un champ vide par défaut. Un envoi vide
    # signifie donc « conserver la valeur actuelle » — on n'efface pas un secret
    # au seul motif que le champ était vide. On ne journalise jamais la valeur.
    if spec.secret and value.strip() == "":
        return config_change_response(success=True, message="Secret inchangé (valeur actuelle conservée).")

    # --- Validation de TOUTES les valeurs AVANT toute mutation (point 10) ---
    if validator == "int":
        if value.isdigit():
            value = int(value)
        else:
            return config_change_response(success=False, message="L'entrée doit être un nombre.")
    else:
        # Le validateur du registre décide : balises {X} autorisées selon la
        # famille de texte (welcome / before_call / after_call / ticket), et
        # balisage d'impression équilibré pour les tickets. Les validateurs
        # sans règle (« text », « bool ») renvoient la valeur inchangée.
        text_check = validate_config_text(key, value)
        if text_check["success"]:
            value = text_check["value"]
        else:
            return config_change_response(success=False, message=text_check["value"])

    # Même garde que les selects pour les clés à choix fermé modifiables via
    # update_input (ex. page_patient_print_fail_behavior, admin_colors).
    if not _closed_value_allowed(spec, value):
        app.logger.warning("Valeur d'input refusée pour %r : %r", key, value)
        return config_change_response(success=False, message="Valeur non autorisée pour ce paramètre.")

    # Les textes de ticket sont stockés en Markdown BRUT uniquement : la
    # conversion ESC/POS se fait à l'impression avec la largeur courante
    # (PRINTER_WIDTH). On n'écrit plus de version préformatée ``*_printer`` —
    # elle était figée à 42 caractères à l'enregistrement et devenait
    # obsolète dès que la largeur d'impression changeait.
    try:
        # MAJ BDD — la colonne cible vient du registre serveur (value_str /
        # value_int / value_text pour les textes longs comme les tickets) ;
        # les autres colonnes sont vidées : une seule source de vérité.
        config_option = ConfigOption.query.filter_by(config_key=key).first()
        if not config_option:
            config_option = ConfigOption(config_key=key)
            db.session.add(config_option)
        for column, column_value in column_values_for(key, value).items():
            setattr(config_option, column, column_value)

        # Point 11 : génération incrémentée dans la même transaction pour la
        # convergence inter-processus (sauf paramètre nécessitant un redémarrage).
        if not spec.restart_required:
            config_sync.bump_generation()

        db.session.commit()
        record_audit(
            ACTION_UPDATE, "config", target_id=key,
            outcome=OUTCOME_SUCCESS,
            # Jamais la valeur d'un secret dans le journal (point 10).
            details="(secret modifié)" if spec.secret else f"value={value}",
        )
    except Exception as e:
        # Toute exception annule la transaction : l'option n'est pas modifiée
        # et app.config reste intact.
        db.session.rollback()
        record_audit(ACTION_UPDATE, "config", target_id=key,
                     outcome=OUTCOME_FAILURE)
        # Pour une clé secrète, ne jamais renvoyer/journaliser le détail technique
        # (il pourrait, selon le backend, contenir la valeur).
        if spec.secret:
            app.logger.error("Échec de mise à jour du paramètre secret %r", key)
            return config_change_response(success=False, message="La mise à jour du secret a échoué.")
        app.logger.error("Échec de mise à jour de l'option %r : %s", key, e)
        return config_change_response(success=False, message="La mise à jour a échoué.")

    # Paramètre nécessitant un redémarrage : persisté mais non appliqué à chaud.
    if spec.restart_required:
        return config_change_response(success=True, message=config_sync.RESTART_REQUIRED_MESSAGE)

    # Commit réussi : refléter en mémoire (app.config) puis effets de bord.
    app.config[spec.config_name] = value
    special_functions_with_input(key)
    # Réponse directe à l'auteur de la requête (pas de diffusion WebSocket à
    # tous les administrateurs pour une sauvegarde de champ individuelle).
    return config_change_response(success=True, message="Option mise à jour.")



def special_functions_with_input(key):
    if key == "cron_delete_patient_table_hour":
        remove_scheduler_clear_all_patients()
        add_scheduler_clear_all_patients()
        communikation("admin", event="refresh_schedule_tasks_list")
    if key == "cron_delete_announce_calls_hour":
        remove_scheduler_clear_announce_calls()
        scheduler_clear_announce_calls()
        communikation("admin", event="refresh_schedule_tasks_list")



@admin_config_bp.route('/admin/update_select', methods=['POST'])
def update_select():
    """ Mise à jour des selects d'options de l'application """
    key = request.values.get('key')
    value = request.values.get('value')

    spec, error = authorize_config_change(key, expected_value_type="value_str")
    if error:
        return error

    # Choix fermé : seules les options déclarées (registre / ressources
    # serveur) sont acceptées — une liste déroulante ne doit pas pouvoir
    # enregistrer une chaîne arbitraire forgée côté client.
    if not _closed_value_allowed(spec, value):
        app.logger.warning("Valeur de select refusée pour %r : %r", key, value)
        return display_toast(success=False, message="Valeur non autorisée pour ce paramètre.")

    # Validation de l'existence de l'option AVANT toute mutation.
    config_option = ConfigOption.query.filter_by(config_key=key).first()
    if not config_option:
        return display_toast(success=False, message="Option non trouvée.")

    try:
        # Mutation en base dans une transaction ; app.config n'est mis à jour
        # qu'APRÈS un commit réussi (point 10).
        config_option.value_str = value
        # Point 11 : génération incrémentée dans la même transaction (convergence
        # inter-processus), sauf paramètre nécessitant un redémarrage.
        if not spec.restart_required:
            config_sync.bump_generation()
        db.session.commit()
        record_audit(ACTION_UPDATE, "config", target_id=key,
                     outcome=OUTCOME_SUCCESS, details=f"value={value}")
    except Exception as e:
        db.session.rollback()
        record_audit(ACTION_UPDATE, "config", target_id=key,
                     outcome=OUTCOME_FAILURE)
        app.logger.error("Échec de mise à jour du select %r : %s", key, e)
        return display_toast(success=False, message="La mise à jour a échoué.")

    # Paramètre nécessitant un redémarrage : persisté mais non appliqué à chaud.
    if spec.restart_required:
        return display_toast(success=True, message=config_sync.RESTART_REQUIRED_MESSAGE)

    app.config[spec.config_name] = value
    call_function_with_select(key, value)
    return display_toast(success=True)



def call_function_with_select(key, value):
    """ Permet d'effectuer une action lors de l'activation d'un select en plus de la sauvegarde"""
    # pour les couleurs, on met la page à jour. Pas possible en js direct, car rechargement trop rapide et on garde donc l'ancienne couleur sur la page
    if key == "admin_colors":
        communikation("admin", event="refresh_colors")


def call_function_with_switch(key, value):
    """ Permet d'effectuer une action lors de l'activation d'un switch en plus de la sauvegarde"""
    if key == "cron_delete_patient_table_activated":
        if value == "true":
            add_scheduler_clear_all_patients()
        else:
            remove_scheduler_clear_all_patients()
    elif key == "cron_delete_announce_calls_activated":
        if value == "true":
            scheduler_clear_announce_calls()
        else:
            remove_scheduler_clear_announce_calls()



def check_balises_before_validation(value):
    """ Permet d'effectuer une action lors de l'activation d'un input en plus de la sauvegarde"""
    app.logger.debug('call_function_with_input %s', value)


def check_balises_after_validation(value):
    """ Permet d'effectuer une action lors de l'activation d'un input en plus de la sauvegarde"""
    app.logger.debug('call_function_with_input %s', value)


@admin_config_bp.route('/admin/database')
@require_permission('schedule')
def admin_database():
    return render_template('/admin/database.html',
                        cron_delete_patient_table_activated = app.config["CRON_DELETE_PATIENT_TABLE_ACTIVATED"],
                        cron_transfer_patient_to_history = app.config["CRON_TRANSFER_PATIENT_TO_HISTORY"],
                        cron_delete_patient_table_hour=app.config["CRON_DELETE_PATIENT_TABLE_HOUR"],
                        cron_delete_announce_calls_activated=app.config["CRON_DELETE_ANNOUNCE_CALLS_ACTIVATED"],
                        cron_delete_announce_calls_hour=app.config["CRON_DELETE_ANNOUNCE_CALLS_HOUR"])




@admin_config_bp.route("/admin/database/schedule_tasks_list")
@require_permission('schedule')
def display_schedule_tasks_list():
    jobs = scheduler.get_jobs()
    main_jobs = []
    other_jobs = []
    
    MAIN_JOBS = ['Clear Patient Table', 'Clear Announce Calls']
    
    for job in jobs:
        # Préparer les informations du job
        job_info = {
            'id': job.id,
            'next_run_time': str(job.next_run_time),
            'function_name': job.func.__name__,
            'trigger': str(job.trigger),
            'misfire_grace_time': job.misfire_grace_time,
            'coalesce': job.coalesce,
            'max_instances': job.max_instances,
            'cron_details': {
                'hour': job.trigger.fields[5] if hasattr(job.trigger, 'fields') else None,
                'minute': job.trigger.fields[4] if hasattr(job.trigger, 'fields') else None,
            }
        }
        
        # Récupérer les 5 dernières exécutions
        last_executions = JobExecutionLog.query.filter_by(
            job_id=job.id
        ).order_by(
            JobExecutionLog.execution_time.desc()
        ).limit(5).all()
        
        job_info['last_executions'] = [{
            'time': log.execution_time,
            'status': log.status,
            'error': log.error_message
        } for log in last_executions]
        
        # Séparer les jobs en deux groupes
        if job.id in MAIN_JOBS:
            main_jobs.append(job_info)
        else:
            other_jobs.append(job_info)
    
    return render_template('/admin/database_schedule_tasks_list.html',
                        main_jobs=main_jobs,
                        other_jobs=other_jobs)



@admin_config_bp.route('/admin/appschedule/dashboard')
@require_permission_dashboard('schedule')
def dashboard_counter():
    # Une seule requête pour la dernière exécution de toutes les tâches (au lieu
    # d'une par tâche — cf. scheduler_dashboard, point 5.3).
    main_jobs_info, other_jobs_info = build_jobs_info(scheduler.get_jobs())

    dashboardcard = DashboardCard.query.filter_by(name="appschedule").first()
    
    return render_template('/admin/dashboard_appschedule.html',
                            dashboardcard=dashboardcard,
                            main_jobs=main_jobs_info,
                            other_jobs=other_jobs_info)



def config_change_response(success=True, message=None):
    """Réponse renvoyée DIRECTEMENT à l'auteur d'une modification de paramètre.

    Contrairement à :func:`display_toast`, qui diffuse le résultat par WebSocket
    à TOUS les administrateurs connectés (chaque admin voit alors un toast pour
    une action qu'il n'a pas faite), cette fonction ne répond qu'au client qui a
    soumis la requête :

    - le **statut HTTP** distingue succès (200) et échec (400), ce qui permet au
      JavaScript (``handleAfterRequestConfig``) de tester ``event.detail.successful``
      et de ne mettre à jour la valeur initiale du champ qu'en cas de succès ;
    - le **corps** contient le message à afficher près du champ concerné.

    Aucune diffusion WebSocket n'est effectuée ici.
    """
    if message is None:
        message = "Enregistré." if success else "Échec de l'enregistrement."
    status = 200 if success else 400
    return message, status, {"Content-Type": "text/plain; charset=utf-8"}


