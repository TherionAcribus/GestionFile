"""Point 4 (audit Admin) — Renforcer la gestion des données.

Quatre problèmes corrigés dans ``routes/admin_data.py`` :

1. **``days`` non validé** dans ``manual_archive`` et ``delete_aggregated`` :
   une valeur négative déplaçait la date de cutoff dans le futur (élargissant
   le périmètre de suppression), et une valeur non entière générait un 500
   avec le détail technique dans la réponse.

2. **``current_app.config`` muté AVANT le commit** dans ``update_config`` :
   si le commit échouait, la mémoire du processus divergeait de la base.

3. **Pas de ``config_sync.bump_generation()``** dans ``update_config`` : les
   autres processus (répliques web, scheduler) n'étaient pas notifiés du
   changement et conservaient une config périmée.

4. **Pas d'audit** sur ces opérations sensibles (archivage, suppression de
   stats, modification de config).

Vérifications statiques (``app.py`` exige MySQL et n'est pas importable ici) :
on lit le source, comme les autres tests de régression statique de ce dépôt.
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _func_body(source, func):
    m = re.search(r"def " + func + r"\(.*?\n(.*?)(?=\ndef |\Z)", source, re.DOTALL)
    assert m, f"fonction {func} introuvable"
    return m.group(1)


# ---------------------------------------------------------------------------
# 1. Validation de `days` : _validate_days existe et borne les valeurs
# ---------------------------------------------------------------------------

def test_validate_days_function_exists():
    source = _read("routes/admin_data.py")
    assert "def _validate_days(" in source
    # Bornes définies
    assert "_DAYS_MIN" in source
    assert "_DAYS_MAX" in source


def test_validate_days_rejects_negative():
    """Une valeur négative ne doit pas produire un cutoff dans le futur."""
    source = _read("routes/admin_data.py")
    body = _func_body(source, "_validate_days")
    # Doit vérifier un minimum strictement positif.
    assert "_DAYS_MIN" in body
    assert "< _DAYS_MIN" in body


def test_validate_days_rejects_non_integer():
    source = _read("routes/admin_data.py")
    body = _func_body(source, "_validate_days")
    # Doit attraper ValueError/TypeError sur int(raw).
    assert "ValueError" in body


def test_manual_archive_uses_validate_days():
    source = _read("routes/admin_data.py")
    body = _func_body(source, "manual_archive")
    assert "_validate_days" in body


def test_delete_aggregated_uses_validate_days():
    source = _read("routes/admin_data.py")
    body = _func_body(source, "delete_aggregated")
    assert "_validate_days" in body


def test_update_config_uses_validate_days():
    source = _read("routes/admin_data.py")
    body = _func_body(source, "update_config")
    assert "_validate_days" in body


# ---------------------------------------------------------------------------
# 2. current_app.config muté APRÈS le commit (et non avant)
# ---------------------------------------------------------------------------

def test_update_config_does_not_mutate_config_before_commit():
    """La mutation de current_app.config doit se faire APRÈS db.session.commit(),
    pas avant, pour ne pas diverger de la base en cas d'échec du commit."""
    source = _read("routes/admin_data.py")
    body = _func_body(source, "update_config")
    # Trouver les positions de la première mutation config et du commit.
    config_pos = body.find("current_app.config[")
    commit_pos = body.find("db.session.commit()")
    rollback_pos = body.find("db.session.rollback()")
    assert config_pos != -1, "current_app.config doit être muté dans update_config"
    assert commit_pos != -1, "db.session.commit() doit être présent"
    # La mutation doit être APRÈS le commit.
    assert config_pos > commit_pos, (
        "current_app.config doit être muté APRÈS db.session.commit(), pas avant. "
        "Sinon, un échec du commit laisse la mémoire diverger de la base."
    )
    # Le rollback doit exister (gestion d'échec).
    assert rollback_pos != -1, "db.session.rollback() doit être présent en cas d'échec"


# ---------------------------------------------------------------------------
# 3. config_sync.bump_generation() dans la transaction
# ---------------------------------------------------------------------------

def test_update_config_bumps_generation():
    """Le changement de config doit incrémenter la génération pour notifier
    les autres processus (répliques web, scheduler)."""
    source = _read("routes/admin_data.py")
    body = _func_body(source, "update_config")
    assert "config_sync.bump_generation()" in body
    # L'import doit être présent.
    assert "import config_sync" in source


# ---------------------------------------------------------------------------
# 4. Audit logging sur les opérations sensibles
# ---------------------------------------------------------------------------

def test_manual_archive_logs_audit():
    source = _read("routes/admin_data.py")
    body = _func_body(source, "manual_archive")
    assert "record_audit" in body


def test_delete_aggregated_logs_audit():
    source = _read("routes/admin_data.py")
    body = _func_body(source, "delete_aggregated")
    assert "record_audit" in body


def test_update_config_logs_audit():
    source = _read("routes/admin_data.py")
    body = _func_body(source, "update_config")
    assert "record_audit" in body


# ---------------------------------------------------------------------------
# 5. Les messages d'erreur ne divulguent pas de détail technique
# ---------------------------------------------------------------------------

def test_error_messages_do_not_leak_exceptions():
    """Les messages d'erreur renvoyés au client ne doivent pas contenir str(e) :
    le détail technique reste dans les journaux serveur."""
    source = _read("routes/admin_data.py")
    # L'ancien code renvoyait str(e) au client. On vérifie qu'aucune route
    # ne renvoie encore le détail de l'exception.
    for func in ("manual_archive", "delete_aggregated", "update_config"):
        body = _func_body(source, func)
        # str(e) ne doit pas apparaître dans un jsonify de réponse d'erreur.
        # (Il peut apparaître dans un logger.error, ce qui est correct.)
        # On vérifie qu'aucun 'message': str(e) ou message=str(e) n'est renvoyé.
        assert not re.search(r"['\"]message['\"]\s*:\s*str\(e\)", body), (
            f"{func} ne doit plus renvoyer str(e) au client (fuite d'info technique)"
        )
        assert not re.search(r"message\s*=\s*str\(e\)", body), (
            f"{func} ne doit plus renvoyer str(e) au client (fuite d'info technique)"
        )
