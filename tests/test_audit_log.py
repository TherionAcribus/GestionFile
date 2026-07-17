"""Journal d'audit des actions sensibles (point 7 — Phase 8) : noyau pur.

Vérifie la normalisation, la redaction et le format de la ligne d'audit, sans
Flask ni base de données.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))

from audit_log import (  # noqa: E402
    build_audit_record,
    format_audit_record,
    ACTION_CREATE,
    ACTION_DELETE,
    OUTCOME_SUCCESS,
    OUTCOME_FAILURE,
    OUTCOME_DENIED,
)


def test_success_record_fields():
    rec = build_audit_record(ACTION_DELETE, "user", user="alice", target_id=42,
                             outcome=OUTCOME_SUCCESS, ip="1.2.3.4")
    assert rec["event"] == "audit"
    assert rec["outcome"] == "success"
    assert rec["user"] == "alice"
    assert rec["action"] == "delete"
    assert rec["resource"] == "user"
    assert rec["target"] == "42"      # l'id entier est rendu en chaîne
    assert rec["ip"] == "1.2.3.4"


def test_details_included_when_present():
    rec = build_audit_record(ACTION_CREATE, "role", details="name=cashier")
    assert rec["details"] == "name=cashier"


def test_details_omitted_when_empty_or_none():
    assert "details" not in build_audit_record(ACTION_CREATE, "role")
    assert "details" not in build_audit_record(ACTION_CREATE, "role", details="")
    assert "details" not in build_audit_record(ACTION_CREATE, "role", details="   ")


def test_missing_fields_become_dash():
    rec = build_audit_record(ACTION_DELETE, "user")
    assert rec["user"] == "-"
    assert rec["target"] == "-"
    assert rec["ip"] == "-"


def test_unknown_outcome_falls_back_to_failure():
    rec = build_audit_record(ACTION_DELETE, "user", outcome="weird")
    assert rec["outcome"] == OUTCOME_FAILURE


def test_denied_outcome_is_preserved():
    rec = build_audit_record(ACTION_DELETE, "user", outcome=OUTCOME_DENIED)
    assert rec["outcome"] == "denied"


def test_newlines_are_stripped_to_prevent_log_injection():
    rec = build_audit_record(ACTION_DELETE, "user",
                             user="alice\naudit outcome=success action=delete",
                             details="line1\r\nline2")
    assert "\n" not in rec["user"] and "\r" not in rec["user"]
    assert "\n" not in rec["details"] and "\r" not in rec["details"]


def test_long_values_are_truncated():
    rec = build_audit_record(ACTION_DELETE, "user", details="A" * 500)
    assert len(rec["details"]) <= 201  # borne + ellipse


def test_no_secret_field_is_ever_recorded():
    rec = build_audit_record(ACTION_CREATE, "user", user="alice", target_id=1,
                             details="username=alice")
    keys = " ".join(rec.keys()).lower()
    assert "password" not in keys
    assert "secret" not in keys
    assert "token" not in keys


def test_format_is_single_line_key_value():
    rec = build_audit_record(ACTION_DELETE, "user", user="alice", target_id=7,
                             outcome=OUTCOME_SUCCESS)
    line = format_audit_record(rec)
    assert line.startswith("audit ")
    assert "\n" not in line
    assert "outcome=success" in line
    assert "action=delete" in line
    assert "resource=user" in line
    assert "target=7" in line
