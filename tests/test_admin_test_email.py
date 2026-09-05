"""Point 4 (audit Admin) — Test email : validation, throttling, audit, erreurs SMTP.

Quatre problèmes corrigés :

1. **Pas de validation d'email** : n'importe quelle chaîne était acceptée et
   transmise à Flask-Mailman, pouvant provoquer une erreur SMTP non gérée.
   Désormais une regex simple valide le format avant l'envoi.

2. **Pas de throttling** : la route pouvait être appelée en boucle pour spammer
   ou utiliser le serveur comme relais SMTP. Désormais 1 test/min/user.

3. **Pas d'audit** : l'envoi d'e-mail de test n'était pas journalisé dans
   l'audit. Désormais ``record_audit`` est appelé en succès ET en échec.

4. **Exception SMTP non gérée** : ``send_test_email`` laissait propager
   l'exception → HTTP 500. Désormais elle capture, journalise et retourne
   ``(False, message_générique)``.

Vérifications statiques (on lit le source, comme les autres tests de
régression statique de ce dépôt).
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _extract_func(source, func_name):
    pattern = rf"def {func_name}\([^)]*\):(.*?)(?=^(?:def |@|\Z))"
    m = re.search(pattern, source, re.DOTALL | re.MULTILINE)
    if not m:
        pytest.fail(f"Fonction {func_name} introuvable")
    return m.group(1)


# ---------------------------------------------------------------------------
# 1. Validation de l'email
# ---------------------------------------------------------------------------

def test_admin_app_mail_test_validates_email():
    """La route doit valider le format de l'email avant l'envoi."""
    source = _read("routes/admin_app.py")
    body = _extract_func(source, "admin_app_mail_test")
    assert "_EMAIL_RE" in body or "re.match" in body or "re.fullmatch" in body


def test_admin_app_has_email_regex():
    """Une regex d'email doit être définie dans admin_app.py."""
    source = _read("routes/admin_app.py")
    assert "_EMAIL_RE" in source
    assert "re.compile" in source


def test_admin_app_mail_test_rejects_empty():
    """Une adresse vide doit être rejetée avec un message clair."""
    source = _read("routes/admin_app.py")
    body = _extract_func(source, "admin_app_mail_test")
    assert "not mail_adress" in body or "not mail_adress.strip()" in body


# ---------------------------------------------------------------------------
# 2. Throttling
# ---------------------------------------------------------------------------

def test_admin_app_mail_test_has_throttling():
    """La route doit limiter la fréquence d'envoi (throttling)."""
    source = _read("routes/admin_app.py")
    body = _extract_func(source, "admin_app_mail_test")
    assert "_email_test_last_sent" in body or "cooldown" in body.lower()
    assert "throttl" in body.lower() or "_email_test_last_sent" in body


def test_admin_app_has_cooldown_constant():
    """Une constante de cooldown doit être définie."""
    source = _read("routes/admin_app.py")
    assert "_EMAIL_TEST_COOLDOWN" in source
    # Doit être un nombre positif (en secondes).
    m = re.search(r"_EMAIL_TEST_COOLDOWN\s*=\s*(\d+(?:\.\d+)?)", source)
    assert m, "_EMAIL_TEST_COOLDOWN doit être un nombre"
    cooldown = float(m.group(1))
    assert cooldown > 0, "Le cooldown doit être positif"


def test_admin_app_throttle_returns_message():
    """En cas de throttle, un message doit être renvoyé (pas un 500)."""
    source = _read("routes/admin_app.py")
    body = _extract_func(source, "admin_app_mail_test")
    assert "patienter" in body.lower() or "attendre" in body.lower()


# ---------------------------------------------------------------------------
# 3. Audit log
# ---------------------------------------------------------------------------

def test_admin_app_mail_test_calls_record_audit():
    """La route doit appeler record_audit pour tracer l'envoi."""
    source = _read("routes/admin_app.py")
    body = _extract_func(source, "admin_app_mail_test")
    assert "record_audit" in body


def test_admin_app_mail_test_audits_success_and_failure():
    """L'audit doit être fait en succès ET en échec."""
    source = _read("routes/admin_app.py")
    body = _extract_func(source, "admin_app_mail_test")
    assert "OUTCOME_SUCCESS" in body
    assert "OUTCOME_FAILURE" in body


def test_admin_app_imports_audit():
    """admin_app.py doit importer record_audit et les constantes d'audit."""
    source = _read("routes/admin_app.py")
    assert "record_audit" in source
    assert "OUTCOME_SUCCESS" in source
    assert "OUTCOME_FAILURE" in source


# ---------------------------------------------------------------------------
# 4. send_test_email capture les exceptions SMTP
# ---------------------------------------------------------------------------

def test_send_test_email_returns_tuple():
    """send_test_email doit retourner (bool, message) au lieu de juste bool."""
    source = _read("routes/admin_security.py")
    body = _extract_func(source, "send_test_email")
    # Doit retourner un tuple (True/False, message)
    assert "return True, None" in body or "return True," in body
    assert "return False," in body


def test_send_test_email_catches_exceptions():
    """send_test_email doit capturer les exceptions SMTP au lieu de les
    laisser propager (ce qui produirait un 500)."""
    source = _read("routes/admin_security.py")
    body = _extract_func(source, "send_test_email")
    assert "try" in body
    assert "except" in body
    assert "logger.error" in body or "logger.warning" in body


def test_send_test_email_no_str_e_to_client():
    """Le message d'erreur renvoyé ne doit pas contenir str(e)."""
    source = _read("routes/admin_security.py")
    body = _extract_func(source, "send_test_email")
    # Vérifier que str(e) n'est pas dans un return.
    returns = re.findall(r'return.*', body)
    for ret in returns:
        assert "str(e)" not in ret, (
            "send_test_email ne doit pas renvoyer str(e) au client"
        )


def test_send_test_email_has_generic_error_message():
    """Le message d'erreur doit être générique (vérifier la configuration SMTP)."""
    source = _read("routes/admin_security.py")
    body = _extract_func(source, "send_test_email")
    assert "SMTP" in body or "configuration" in body.lower()


# ---------------------------------------------------------------------------
# 5. La route gère le nouveau retour tuple de send_test_email
# ---------------------------------------------------------------------------

def test_admin_app_mail_test_unpacks_tuple():
    """La route doit déballer le tuple (success, error_msg) retourné par
    send_test_email."""
    source = _read("routes/admin_app.py")
    body = _extract_func(source, "admin_app_mail_test")
    # L'ancien code faisait `if send_test_email(mail_adress):`
    # Le nouveau doit faire `success, error_msg = send_test_email(...)`
    assert "success, " in body or "success," in body
