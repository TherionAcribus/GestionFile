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
    ("routes/admin_queue.py", "clear_all_patients_from_db"),
    ("routes/admin_queue.py", "delete_patient"),
    ("routes/admin_backup.py", "backup_import"),
    ("routes/admin_backup.py", "backup_import_multi"),
    ("routes/admin_backup.py", "backup_import_single"),
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
