"""Échéances serveur des patients laissés trop longtemps en appel."""

from datetime import datetime, timedelta

from flask import current_app

from communication import communikation
from config import time_tz
from models import Patient, db


def _now_local_naive(now=None):
    value = now or datetime.now(time_tz)
    if value.tzinfo is not None:
        value = value.astimezone(time_tz).replace(tzinfo=None)
    return value


def enabled():
    return bool(current_app.config.get("CALL_AUTO_VALIDATION_ENABLED", False))


def client_config():
    return {
        "enabled": enabled(),
        "warning_seconds": int(current_app.config.get(
            "CALL_AUTO_VALIDATION_WARNING_SECONDS", 60)),
        "grace_seconds": int(current_app.config.get(
            "CALL_AUTO_VALIDATION_GRACE_SECONDS", 30)),
        "extension_seconds": int(current_app.config.get(
            "CALL_AUTO_VALIDATION_EXTENSION_SECONDS", 60)),
    }


def values_for_new_call(now=None):
    if not enabled():
        return {
            "call_auto_validate_at": None,
            "call_extension_count": 0,
        }
    config = client_config()
    deadline = _now_local_naive(now) + timedelta(
        seconds=config["warning_seconds"] + config["grace_seconds"])
    return {
        "call_auto_validate_at": deadline,
        "call_extension_count": 0,
    }


def extend_call(counter_id, patient_id, now=None):
    if not enabled():
        return False, {"error": "auto_validation_disabled"}, 409

    patient = db.session.get(Patient, patient_id)
    if patient is None:
        return False, {"error": "not_found"}, 404
    if patient.status != "calling" or patient.counter_id != counter_id:
        return False, {"error": "invalid_state"}, 409
    if patient.call_auto_validate_at is None:
        return False, {"error": "deadline_not_armed"}, 409

    patient.call_auto_validate_at = _now_local_naive(now) + timedelta(
        seconds=client_config()["extension_seconds"])
    patient.call_extension_count = (patient.call_extension_count or 0) + 1
    db.session.commit()

    communikation(
        "app_counter",
        event="counter_state_changed",
        flag=counter_id,
        data={"counter_id": counter_id, "reason": "call_extended"},
    )
    return True, patient.to_dict(), 200


def auto_validate_expired(now=None):
    if not enabled():
        return []

    current = _now_local_naive(now)
    candidate_ids = [row.id for row in (
        Patient.query
        .filter(
            Patient.status == "calling",
            Patient.call_auto_validate_at.isnot(None),
            Patient.call_auto_validate_at <= current,
        )
        .with_entities(Patient.id)
        .all()
    )]
    validated = []
    for patient_id in candidate_ids:
        updated = (
            db.session.query(Patient)
            .filter(
                Patient.id == patient_id,
                Patient.status == "calling",
                Patient.call_auto_validate_at.isnot(None),
                Patient.call_auto_validate_at <= current,
            )
            .update({
                "status": "ongoing",
                "timestamp_counter": datetime.now(time_tz),
                "call_auto_validate_at": None,
            }, synchronize_session=False)
        )
        if updated:
            validated.append(patient_id)

    if not validated:
        db.session.rollback()
        return []

    rows = Patient.query.filter(Patient.id.in_(validated)).all()
    counter_ids = {patient.counter_id for patient in rows}
    db.session.commit()

    communikation("update_patient")
    for patient in rows:
        communikation(
            "update_screen", event="remove_calling", data={"id": patient.id})
    for counter_id in counter_ids:
        communikation(
            "app_counter",
            event="counter_state_changed",
            flag=counter_id,
            data={"counter_id": counter_id, "reason": "call_auto_validated"},
        )
    return validated


def disable_and_clear_deadlines():
    updated = (
        db.session.query(Patient)
        .filter(Patient.call_auto_validate_at.isnot(None))
        .update({"call_auto_validate_at": None}, synchronize_session=False)
    )
    db.session.commit()
    return updated


def announce_config_changed():
    communikation(
        "app_counter",
        event="counter_state_changed",
        data={"reason": "call_auto_validation_config_changed"},
    )
