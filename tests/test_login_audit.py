"""Journal d'audit des connexions (point 3.4) — logique pure et redaction."""

from login_audit import (
    build_login_audit,
    format_login_audit,
    OUTCOME_SUCCESS,
    OUTCOME_FAILURE,
    OUTCOME_BLOCKED,
)


def test_success_record_fields():
    rec = build_login_audit(OUTCOME_SUCCESS, username="alice", ip="1.2.3.4",
                            user_agent="Mozilla/5.0")
    assert rec["event"] == "login"
    assert rec["outcome"] == "success"
    assert rec["username"] == "alice"
    assert rec["ip"] == "1.2.3.4"
    assert "retry_after_s" not in rec  # pas de délai sur un succès


def test_blocked_record_includes_retry_after():
    rec = build_login_audit(OUTCOME_BLOCKED, username="alice", ip="1.2.3.4",
                            retry_after=42.7)
    assert rec["outcome"] == "blocked"
    assert rec["retry_after_s"] == 42  # arrondi entier


def test_unknown_outcome_falls_back_to_failure():
    rec = build_login_audit("weird", username="x")
    assert rec["outcome"] == OUTCOME_FAILURE


def test_missing_fields_become_dash():
    rec = build_login_audit(OUTCOME_FAILURE)
    assert rec["username"] == "-"
    assert rec["ip"] == "-"
    assert rec["user_agent"] == "-"


def test_newlines_are_stripped_to_prevent_log_injection():
    rec = build_login_audit(OUTCOME_FAILURE,
                            username="alice\nauth outcome=success ip=evil")
    assert "\n" not in rec["username"]
    assert "\r" not in rec["username"]


def test_long_values_are_truncated():
    rec = build_login_audit(OUTCOME_FAILURE, user_agent="A" * 500)
    assert len(rec["user_agent"]) <= 121  # borne + ellipse


def test_no_password_field_is_ever_recorded():
    rec = build_login_audit(OUTCOME_FAILURE, username="alice", ip="1.2.3.4")
    # Aucun champ ne doit ressembler à un secret.
    keys = " ".join(rec.keys()).lower()
    assert "password" not in keys
    assert "secret" not in keys
    assert "token" not in keys


def test_format_is_single_line_key_value():
    rec = build_login_audit(OUTCOME_SUCCESS, username="alice", ip="1.2.3.4")
    line = format_login_audit(rec)
    assert line.startswith("auth ")
    assert "\n" not in line
    assert "outcome=success" in line
    assert "username=alice" in line
