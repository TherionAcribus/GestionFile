"""Durcissement de la route de connexion (point 3.4) — régression statique.

La route ``login`` vit dans ``routes/admin_security.py``, dont l'import complet
tire ``app.py`` (donc MySQL). On vérifie donc le durcissement sur la **source**,
comme le fait déjà ``test_admin_auth_required.py`` pour app.py. Les décisions
elles-mêmes (limitation, audit, politique) sont couvertes unitairement par
``test_login_throttle.py``, ``test_login_audit.py`` et ``test_password_policy.py``.
"""

import os
import re

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _login_body(source):
    m = re.search(r"def login\(\):(.*?)\n# Hash bcrypt figé", source, re.DOTALL)
    assert m, "Corps de la fonction login introuvable"
    return m.group(1)


def test_generic_error_message_defined():
    source = _read("routes/admin_security.py")
    assert 'GENERIC_LOGIN_ERROR = "Identifiants incorrects"' in source


def test_no_username_enumeration_messages():
    """Les messages qui révélaient lequel de l'identifiant/mot de passe est faux
    ne doivent plus exister (anti-énumération)."""
    body = _login_body(_read("routes/admin_security.py"))
    assert "Nom d'utilisateur inconnu" not in body
    assert "Mot de passe incorrect" not in body


def test_login_uses_generic_message_only():
    body = _login_body(_read("routes/admin_security.py"))
    # Tous les refus passent par le message générique.
    assert "GENERIC_LOGIN_ERROR" in body


def test_login_checks_throttle_before_password():
    body = _login_body(_read("routes/admin_security.py"))
    idx_block = body.index("worst_retry_after(")
    idx_verify = body.index("verify_password(")
    assert idx_block < idx_verify, (
        "La limitation doit être évaluée AVANT la vérification du mot de passe")


def test_login_dummy_check_for_unknown_user():
    body = _login_body(_read("routes/admin_security.py"))
    assert "_dummy_password_check(" in body, (
        "Un utilisateur inconnu doit déclencher une vérification factice (anti-timing)")


def test_login_registers_failure_on_both_keys():
    body = _login_body(_read("routes/admin_security.py"))
    assert "register_failure(ip_key(" in body
    assert "register_failure(identity_key(" in body


def test_login_resets_throttle_on_success():
    body = _login_body(_read("routes/admin_security.py"))
    assert "register_success(ip_key(" in body
    assert "register_success(identity_key(" in body


def test_login_audits_all_outcomes():
    body = _login_body(_read("routes/admin_security.py"))
    assert "OUTCOME_BLOCKED" in body
    assert "OUTCOME_FAILURE" in body
    assert "OUTCOME_SUCCESS" in body


def test_client_ip_does_not_trust_forwarded_header():
    source = _read("routes/admin_security.py")
    m = re.search(r"def _client_ip\(\):(.*?)\n\ndef ", source, re.DOTALL)
    assert m, "_client_ip introuvable"
    body = m.group(1)
    assert "request.remote_addr" in body
    assert "X-Forwarded-For" not in body or "ne fait PAS confiance" in body


def test_password_policy_wired_into_user_creation():
    source = _read("routes/admin_security.py")
    m = re.search(r"def add_new_user\(\):(.*?)def security_update_user", source, re.DOTALL)
    assert m, "add_new_user introuvable"
    assert "validate_password(" in m.group(1)


def test_password_policy_wired_into_password_change():
    source = _read("routes/admin_security.py")
    m = re.search(r"def update_password\(user_id\):(.*?)def create_default_user", source, re.DOTALL)
    assert m, "update_password introuvable"
    assert "validate_password(" in m.group(1)
