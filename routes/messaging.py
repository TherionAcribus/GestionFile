"""API autoritative de messagerie pour l'App Comptoir."""

import json

from flask import Blueprint, jsonify, request

from auth_utils import require_app_token
from services import messaging_service as messaging


messaging_bp = Blueprint("messaging", __name__)


def _json():
    return request.get_json(silent=True) or request.form.to_dict() or {}


def _error(exc):
    body = {"error": exc.code}
    if getattr(exc, "message", None):
        body["message"] = exc.message
    return jsonify(body), exc.status


@messaging_bp.route("/api/messaging/presence", methods=["POST"])
@require_app_token
def presence():
    data = _json()
    try:
        return jsonify(messaging.heartbeat(
            data.get("counter_id"), data.get("client_instance_id"),
        ))
    except messaging.MessagingError as exc:
        return _error(exc)


@messaging_bp.route("/api/messaging/presence/leave", methods=["POST"])
@require_app_token
def presence_leave():
    data = _json()
    try:
        return jsonify(messaging.leave(
            data.get("counter_id"), data.get("client_instance_id"),
        ))
    except messaging.MessagingError as exc:
        return _error(exc)


@messaging_bp.route("/api/messaging/state", methods=["GET"])
@require_app_token
def messaging_state():
    if not messaging.enabled():
        return jsonify({"enabled": False})
    try:
        return jsonify(messaging.state(request.args.get("counter_id")))
    except messaging.MessagingError as exc:
        return _error(exc)


@messaging_bp.route("/api/messaging/messages", methods=["GET", "POST"])
@require_app_token
def messages():
    try:
        if request.method == "POST":
            data = _json()
            result, created = messaging.send_message(data.get("counter_id"), data)
            return jsonify(result), 201 if created else 200
        return jsonify(messaging.list_messages(
            request.args.get("counter_id"),
            kind=request.args.get("kind"),
            peer_staff_id=request.args.get("peer_staff_id"),
            before_id=request.args.get("before_id"),
            after_id=request.args.get("after_id"),
            limit=request.args.get("limit", messaging.PAGE_SIZE_DEFAULT),
        ))
    except messaging.MessagingError as exc:
        return _error(exc)


@messaging_bp.route("/api/messaging/delete", methods=["POST"])
@require_app_token
def delete_message():
    data = _json()
    try:
        return jsonify(messaging.delete_message(
            data.get("counter_id"), data.get("message_id"),
        ))
    except messaging.MessagingError as exc:
        return _error(exc)


@messaging_bp.route("/api/messaging/read", methods=["POST"])
@require_app_token
def read_messages():
    data = _json()
    message_ids = data.get("message_ids")
    if isinstance(message_ids, str):
        try:
            message_ids = json.loads(message_ids)
        except (TypeError, ValueError):
            message_ids = None
    try:
        return jsonify(messaging.mark_read(
            data.get("counter_id"), message_ids,
        ))
    except messaging.MessagingError as exc:
        return _error(exc)


@messaging_bp.route("/api/messaging/quick_replies", methods=["PUT"])
@require_app_token
def save_quick_replies():
    """Phrases rapides personnelles de l'équipier connecté au comptoir.
    Corps JSON : {"counter_id": int, "personal": [str, ...]}."""
    data = request.get_json(silent=True) or {}
    try:
        return jsonify(messaging.save_quick_replies(
            data.get("counter_id"), data.get("personal"),
        ))
    except messaging.MessagingError as exc:
        return _error(exc)
