"""Journal d'audit (point 7 — Phase 8) : câblage sur les actions sensibles.

Régression statique (sans MySQL ni serveur) : chaque action sensible instrumentée
doit continuer d'appeler ``record_audit`` dans son corps. On analyse la source
par AST plutôt que par simple grep, pour attacher chaque appel à la bonne
fonction (et non au fichier entier). Empêche qu'une réécriture retire par
mégarde une trace d'audit.
"""

import ast
import os

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _function_calls_record_audit(source, func_name):
    """True si la fonction ``func_name`` (ou une fonction imbriquée) appelle
    ``record_audit`` quelque part dans son corps."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    if isinstance(fn, ast.Name) and fn.id == "record_audit":
                        return True
            return False
    raise AssertionError(f"Fonction introuvable: {func_name}")


# (fichier, fonction) des actions sensibles instrumentées au point 7.
_AUDITED = [
    ("routes/admin_security.py", "add_new_user"),
    ("routes/admin_security.py", "security_update_user"),
    ("routes/admin_security.py", "delete_user2"),
    ("routes/admin_security.py", "security_update_role"),
    ("routes/admin_security.py", "save_role"),
    ("routes/admin_security.py", "delete_role"),
    ("routes/admin_security.py", "update_password"),
    ("routes/admin_security.py", "reset_admin"),
    ("routes/admin_security.py", "logout_all"),
    # Purge de la file : le métier (et son audit succès/échec) vit dans le
    # service depuis le découplage route/planificateur — la vue appelle le
    # service, l'audit reste donc garanti pour les deux appelants. Les
    # fonctions publiques délèguent toutes à ces deux noyaux audités (garde
    # de délégation dans test_queue_purge).
    ("services/queue_service.py", "_purge"),
    ("services/queue_service.py", "_archive_and_purge"),
    ("routes/admin_queue.py", "delete_patient"),
    ("routes/admin_backup.py", "backup_import"),
    ("routes/admin_backup.py", "backup_import_multi"),
    ("routes/admin_backup.py", "backup_import_single"),
    # Couverture élargie (point « journal incomplet ») : chaque mutation Admin
    # ci-dessous doit tracer son succès — et son échec lorsqu'un bloc except
    # existe (rollback avant record_audit, pour ne pas persister l'état partiel).
    ("routes/admin_counter.py", "add_new_counter"),
    ("routes/admin_counter.py", "update_counter"),
    ("routes/admin_counter.py", "delete_counter"),
    ("routes/admin_counter.py", "update_counter_order"),
    ("routes/admin_schedule.py", "add_new_schedule"),
    ("routes/admin_schedule.py", "update_schedule"),
    ("routes/admin_schedule.py", "delete_schedule"),
    ("routes/admin_activity.py", "add_new_activity"),
    ("routes/admin_activity.py", "update_activity"),
    ("routes/admin_activity.py", "delete_activity"),
    ("routes/admin_staff.py", "add_new_staff"),
    ("routes/admin_staff.py", "update_member"),
    ("routes/admin_staff.py", "delete_staff"),
    ("routes/admin_staff.py", "add_counter"),
    ("routes/admin_staff.py", "add_pharmacist"),
    ("routes/admin_staff.py", "update_pharmacist"),
    ("routes/admin_translation.py", "add_new_language"),
    ("routes/admin_translation.py", "update_language"),
    ("routes/admin_translation.py", "delete_language"),
    ("routes/admin_translation.py", "update_languages_order"),
    ("routes/admin_translation.py", "translations_collect"),
    ("routes/admin_translation.py", "save_translations"),
    ("routes/admin_translation.py", "upload_flag_image"),
    ("routes/admin_patient.py", "add_new_button"),
    ("routes/admin_patient.py", "update_button"),
    ("routes/admin_patient.py", "update_button_order"),
    ("routes/admin_patient.py", "delete_button"),
    ("routes/admin_patient.py", "activate_button"),
    ("routes/admin_patient.py", "deactivate_button"),
    ("routes/admin_patient.py", "upload_image"),
    ("routes/admin_patient.py", "upload_image_for_interface"),
    ("routes/admin_patient.py", "update_button_image_from_gallery"),
    ("routes/admin_patient.py", "update_button_image_from_gallery_for_interface"),
    ("routes/admin_patient.py", "delete_button_image"),
    ("routes/admin_announce.py", "select_signal"),
    ("routes/admin_announce.py", "delete_sound"),
    ("routes/admin_announce.py", "upload_signal_file"),
    ("routes/admin_announce.py", "upload_google_key"),
    ("routes/admin_announce.py", "announce_save_voice_model"),
    ("routes/admin_announce.py", "announce_save_google_voice"),
    ("routes/admin_announce.py", "announce_save_gtts_voice"),
    ("routes/admin_announce.py", "announce_save_voice_is_active"),
    ("routes/admin_dashboard.py", "hide_dashboard_card"),
    ("routes/admin_dashboard.py", "dashboard_valid_select"),
    ("routes/admin_dashboard.py", "save_dashboard_order"),
    ("routes/admin_dashboard.py", "resize_dashboard_card"),
    ("routes/admin_dashboard.py", "add_dashboard_card"),
    ("routes/admin_dashboard.py", "save_dashboard_configuration"),
    ("routes/admin_gallery.py", "choose_gallery"),
    ("routes/admin_gallery.py", "upload_gallery"),
    ("routes/admin_gallery.py", "delete_image"),
    ("routes/admin_gallery.py", "delete_gallery"),
    ("routes/admin_gallery.py", "create_gallery"),
    ("routes/admin_music.py", "change_volume"),
    ("routes/admin_music.py", "spotify_callback"),
    ("routes/admin_music.py", "spotify_logout"),
    ("routes/admin_algo.py", "toggle_activation"),
    ("routes/admin_algo.py", "change_overtaken_limit"),
    ("routes/admin_algo.py", "add_new_rule"),
    ("routes/admin_algo.py", "delete_algo"),
    ("routes/admin_algo.py", "update_algo_rule"),
    ("routes/admin_config.py", "update_switch"),
    ("routes/admin_config.py", "update_input"),
    ("routes/admin_config.py", "update_select"),
    ("routes/admin_config.py", "update_css_variable"),
    ("routes/admin_config.py", "copy_colors"),
]


def test_sensitive_actions_call_record_audit():
    sources = {}
    for rel, func in _AUDITED:
        sources.setdefault(rel, _read(rel))
        assert _function_calls_record_audit(sources[rel], func), (
            f"{rel}::{func} ne consigne plus l'action via record_audit")


def test_audit_model_and_migration_exist():
    models_src = _read("models.py")
    assert "class AuditLog(db.Model)" in models_src
    assert "__tablename__ = 'audit_log'" in models_src
    # La migration crée la table audit_log.
    mig = _read("migrations/versions/d7e8f9a0b1c2_add_audit_log.py")
    assert "create_table(\n        'audit_log'" in mig or "'audit_log'" in mig


def test_no_secret_columns_in_audit_model():
    """Le modèle d'audit ne doit exposer aucune colonne de secret.

    On n'inspecte que les lignes de définition de colonnes (``db.Column``) : la
    docstring, elle, mentionne légitimement l'absence de secret.
    """
    models_src = _read("models.py")
    start = models_src.index("class AuditLog(db.Model)")
    end = models_src.index("class JobExecutionLog(db.Model)")
    column_lines = [
        ln.lower() for ln in models_src[start:end].splitlines()
        if "db.column" in ln.lower()
    ]
    assert column_lines, "aucune colonne détectée dans AuditLog"
    for ln in column_lines:
        for forbidden in ("password", "secret", "token"):
            assert forbidden not in ln, f"colonne interdite dans AuditLog: {forbidden!r} ({ln.strip()})"
