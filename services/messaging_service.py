"""Métier de la messagerie interne des App Comptoir.

La base est la source autoritative. Socket.IO ne transporte que des signaux
d'invalidation sans le texte des messages.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from communication import communikation
from config import time_tz
from models import (
    AppMessage, AppMessageRecipient, AppMessagingPresence,
    Counter, Pharmacist, db,
)


PRESENCE_TTL_SECONDS = 90
MESSAGE_RETENTION_DAYS = 7
MESSAGE_MAX_LENGTH = 1000
PAGE_SIZE_DEFAULT = 50
PAGE_SIZE_MAX = 100


class MessagingError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code = code
        self.status = status


def now():
    """Instant UTC naïf, format retenu par les colonnes DATETIME MySQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enabled() -> bool:
    return bool(current_app.config.get("APP_MESSAGING_ENABLED", False))


def require_enabled():
    if not enabled():
        raise MessagingError("messaging_disabled", 403)


def _valid_uuid(value) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise MessagingError("invalid_message", 400)


def resolve_identity(counter_id):
    try:
        counter_id = int(counter_id)
    except (TypeError, ValueError):
        raise MessagingError("staff_not_connected", 409)
    counter = db.session.get(Counter, counter_id)
    if not counter or not counter.staff:
        raise MessagingError("staff_not_connected", 409)
    return counter, counter.staff


def _active_cutoff():
    return now() - timedelta(seconds=PRESENCE_TTL_SECONDS)


def _active_presences():
    return (
        AppMessagingPresence.query
        .filter(AppMessagingPresence.last_seen_at >= _active_cutoff())
        .options(
            joinedload(AppMessagingPresence.staff),
            joinedload(AppMessagingPresence.counter),
        )
        .order_by(AppMessagingPresence.last_seen_at.desc())
        .all()
    )


def _active_people(exclude_staff_id=None):
    people = {}
    for presence in _active_presences():
        if presence.staff_id == exclude_staff_id or not presence.staff or not presence.counter:
            continue
        people.setdefault(presence.staff_id, {
            "staff_id": presence.staff_id,
            "name": presence.staff.name,
            "counter_id": presence.counter_id,
            "counter_name": presence.counter.name,
            "online": True,
        })
    return people


def _emit(event, data=None):
    communikation("app_counter", data=data, event=event)


def heartbeat(counter_id, client_instance_id):
    require_enabled()
    counter, staff = resolve_identity(counter_id)
    client_instance_id = _valid_uuid(client_instance_id)
    current = now()
    presence = db.session.get(AppMessagingPresence, client_instance_id)
    created = presence is None
    changed = created or presence.staff_id != staff.id or presence.counter_id != counter.id
    if created:
        presence = AppMessagingPresence(
            client_instance_id=client_instance_id,
            staff_id=staff.id,
            counter_id=counter.id,
            connected_at=current,
            last_seen_at=current,
        )
        db.session.add(presence)
    else:
        if changed:
            presence.connected_at = current
        presence.staff_id = staff.id
        presence.counter_id = counter.id
        presence.last_seen_at = current
    db.session.commit()
    if changed:
        _emit("messaging_presence_changed")
    return state(counter.id)


def leave(counter_id, client_instance_id):
    require_enabled()
    _counter, staff = resolve_identity(counter_id)
    client_instance_id = _valid_uuid(client_instance_id)
    presence = db.session.get(AppMessagingPresence, client_instance_id)
    if presence and presence.staff_id == staff.id:
        db.session.delete(presence)
        db.session.commit()
        _emit("messaging_presence_changed")
    return {"enabled": True}


def disable_and_clear_presence():
    AppMessagingPresence.query.delete(synchronize_session=False)
    db.session.commit()
    _emit("messaging_config_changed", {"enabled": False})


def announce_enabled():
    _emit("messaging_config_changed", {"enabled": True})


def _today_start():
    # L'historique par défaut correspond à la journée locale de l'équipe,
    # tandis que les dates persistées restent strictement en UTC.
    local = datetime.now(time_tz)
    local_midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def _visible_query(staff_id):
    return AppMessage.query.filter(
        AppMessage.created_at >= _today_start(),
        or_(
            AppMessage.sender_staff_id == staff_id,
            AppMessage.recipients.any(
                AppMessageRecipient.recipient_staff_id == staff_id,
            ),
        ),
    )


def _iso(value):
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _recipient_payload(recipient):
    return {
        "staff_id": recipient.recipient_staff_id,
        "name": recipient.recipient_name,
        "counter_id": recipient.recipient_counter_id,
        "counter_name": recipient.recipient_counter_name,
        "read_at": _iso(recipient.read_at),
    }


def serialize_message(message, viewer_staff_id):
    recipients = [_recipient_payload(row) for row in message.recipients]
    viewer_recipient = next(
        (row for row in message.recipients if row.recipient_staff_id == viewer_staff_id),
        None,
    )
    direct_read_at = None
    if message.kind == "direct" and message.sender_staff_id == viewer_staff_id:
        direct_read_at = _iso(message.recipients[0].read_at) if message.recipients else None
    return {
        "id": message.id,
        "client_message_id": message.client_message_id,
        "kind": message.kind,
        "body": message.body,
        "created_at": _iso(message.created_at),
        "sender": {
            "staff_id": message.sender_staff_id,
            "name": message.sender_name,
            "counter_id": message.sender_counter_id,
            "counter_name": message.sender_counter_name,
        },
        "recipients": recipients if message.kind == "direct" else [],
        "read_at": direct_read_at,
        "is_unread": bool(viewer_recipient and not viewer_recipient.read_at),
    }


def state(counter_id):
    require_enabled()
    counter, staff = resolve_identity(counter_id)
    people = _active_people(exclude_staff_id=staff.id)
    recent = (
        _visible_query(staff.id)
        .options(joinedload(AppMessage.recipients))
        .order_by(AppMessage.id.desc())
        .limit(500)
        .all()
    )
    conversations = {
        "broadcast": {
            "key": "broadcast", "kind": "broadcast", "name": "Toute l’équipe",
            "online": True, "unread_count": 0, "last_message_id": None,
            "last_message_at": None,
        }
    }
    for message in recent:
        if message.kind == "broadcast":
            summary = conversations["broadcast"]
        else:
            if message.sender_staff_id == staff.id:
                recipient = message.recipients[0] if message.recipients else None
                if not recipient or recipient.recipient_staff_id is None:
                    continue
                peer_id = recipient.recipient_staff_id
                peer = people.get(peer_id, {
                    "staff_id": peer_id,
                    "name": recipient.recipient_name,
                    "counter_id": recipient.recipient_counter_id,
                    "counter_name": recipient.recipient_counter_name,
                    "online": False,
                })
            else:
                peer_id = message.sender_staff_id
                if peer_id is None:
                    continue
                peer = people.get(peer_id, {
                    "staff_id": peer_id,
                    "name": message.sender_name,
                    "counter_id": message.sender_counter_id,
                    "counter_name": message.sender_counter_name,
                    "online": False,
                })
            key = f"direct:{peer_id}"
            summary = conversations.setdefault(key, {
                "key": key, "kind": "direct", **peer,
                "unread_count": 0, "last_message_id": None,
                "last_message_at": None,
            })
        if summary["last_message_id"] is None:
            summary["last_message_id"] = message.id
            summary["last_message_at"] = _iso(message.created_at)
        recipient = next(
            (row for row in message.recipients if row.recipient_staff_id == staff.id),
            None,
        )
        if recipient and recipient.read_at is None:
            summary["unread_count"] += 1

    for peer_id, peer in people.items():
        key = f"direct:{peer_id}"
        conversations.setdefault(key, {
            "key": key, "kind": "direct", **peer,
            "unread_count": 0, "last_message_id": None, "last_message_at": None,
        })

    ordered = sorted(
        conversations.values(),
        key=lambda item: (
            item["key"] != "broadcast",
            item["online"],
            item["last_message_id"] or 0,
        ),
        reverse=True,
    )
    broadcast = next(item for item in ordered if item["key"] == "broadcast")
    ordered = [broadcast] + [item for item in ordered if item["key"] != "broadcast"]
    return {
        "enabled": True,
        "self": {
            "staff_id": staff.id, "name": staff.name,
            "counter_id": counter.id, "counter_name": counter.name,
        },
        "conversations": ordered,
        "unread_total": sum(item["unread_count"] for item in ordered),
        "online_count": len(people),
        "server_time": _iso(now()),
    }


def list_messages(counter_id, *, kind, peer_staff_id=None, before_id=None,
                  after_id=None, limit=PAGE_SIZE_DEFAULT):
    require_enabled()
    _counter, staff = resolve_identity(counter_id)
    if kind not in {"direct", "broadcast"}:
        raise MessagingError("invalid_message", 400)
    try:
        limit = max(1, min(PAGE_SIZE_MAX, int(limit)))
    except (TypeError, ValueError):
        limit = PAGE_SIZE_DEFAULT
    query = _visible_query(staff.id).filter(AppMessage.kind == kind)
    if kind == "direct":
        try:
            peer_staff_id = int(peer_staff_id)
        except (TypeError, ValueError):
            raise MessagingError("invalid_message", 400)
        query = query.filter(or_(
            and_(
                AppMessage.sender_staff_id == staff.id,
                AppMessage.recipients.any(
                    AppMessageRecipient.recipient_staff_id == peer_staff_id,
                ),
            ),
            and_(
                AppMessage.sender_staff_id == peer_staff_id,
                AppMessage.recipients.any(
                    AppMessageRecipient.recipient_staff_id == staff.id,
                ),
            ),
        ))
    query = query.options(joinedload(AppMessage.recipients))
    try:
        if after_id is not None:
            query = query.filter(AppMessage.id > int(after_id)).order_by(AppMessage.id.asc())
            rows = query.limit(limit).all()
        else:
            if before_id is not None:
                query = query.filter(AppMessage.id < int(before_id))
            rows = query.order_by(AppMessage.id.desc()).limit(limit).all()
            rows.reverse()
    except (TypeError, ValueError):
        raise MessagingError("invalid_message", 400)
    return {
        "messages": [serialize_message(row, staff.id) for row in rows],
        "has_more": len(rows) == limit,
    }


def send_message(counter_id, payload):
    require_enabled()
    counter, staff = resolve_identity(counter_id)
    client_message_id = _valid_uuid(payload.get("client_message_id"))
    existing = AppMessage.query.filter_by(client_message_id=client_message_id).first()
    if existing:
        if existing.sender_staff_id != staff.id:
            raise MessagingError("invalid_message", 400)
        return serialize_message(existing, staff.id), False

    kind = str(payload.get("kind") or "")
    body = str(payload.get("body") or "").strip()
    if kind not in {"direct", "broadcast"} or not body or len(body) > MESSAGE_MAX_LENGTH:
        raise MessagingError("invalid_message", 400)

    active = _active_people(exclude_staff_id=staff.id)
    if kind == "direct":
        try:
            recipient_id = int(payload.get("recipient_staff_id"))
        except (TypeError, ValueError):
            raise MessagingError("invalid_message", 400)
        recipient = active.get(recipient_id)
        if recipient is None:
            raise MessagingError("recipient_offline", 409)
        recipients = [recipient]
    else:
        recipients = list(active.values())
        if not recipients:
            raise MessagingError("recipient_offline", 409)

    message = AppMessage(
        client_message_id=client_message_id,
        kind=kind,
        sender_staff_id=staff.id,
        sender_counter_id=counter.id,
        sender_name=staff.name,
        sender_counter_name=counter.name,
        body=body,
    )
    for recipient in recipients:
        message.recipients.append(AppMessageRecipient(
            recipient_staff_id=recipient["staff_id"],
            recipient_counter_id=recipient["counter_id"],
            recipient_name=recipient["name"],
            recipient_counter_name=recipient["counter_name"],
        ))
    db.session.add(message)
    try:
        db.session.commit()
    except IntegrityError:
        # Deux tentatives du même envoi peuvent arriver simultanément après
        # une coupure. L'unicité en base tranche ; le perdant relit la réponse
        # déjà validée au lieu de créer un doublon ou de renvoyer une 500.
        db.session.rollback()
        existing = AppMessage.query.filter_by(
            client_message_id=client_message_id,
        ).first()
        if existing and existing.sender_staff_id == staff.id:
            return serialize_message(existing, staff.id), False
        raise MessagingError("invalid_message", 400)
    _emit("messaging_changed", {
        "message_id": message.id,
        "kind": message.kind,
        "sender_staff_id": staff.id,
        "sender_name": staff.name,
    })
    result = serialize_message(message, staff.id)
    result["recipient_count"] = len(recipients)
    return result, True


def mark_read(counter_id, message_ids):
    require_enabled()
    _counter, staff = resolve_identity(counter_id)
    if not isinstance(message_ids, list) or not message_ids or len(message_ids) > 100:
        raise MessagingError("invalid_message", 400)
    try:
        ids = {int(value) for value in message_ids}
    except (TypeError, ValueError):
        raise MessagingError("invalid_message", 400)
    rows = AppMessageRecipient.query.filter(
        AppMessageRecipient.recipient_staff_id == staff.id,
        AppMessageRecipient.message_id.in_(ids),
        AppMessageRecipient.read_at.is_(None),
    ).all()
    read_at = now()
    for row in rows:
        row.read_at = read_at
    if rows:
        db.session.commit()
        _emit("messaging_changed", {"read": True})
    return {"read": len(rows), "read_at": _iso(read_at) if rows else None}


def purge_expired():
    cutoff = now() - timedelta(days=MESSAGE_RETENTION_DAYS)
    deleted = AppMessage.query.filter(AppMessage.created_at < cutoff).delete(
        synchronize_session=False,
    )
    AppMessagingPresence.query.filter(
        AppMessagingPresence.last_seen_at < now() - timedelta(days=1),
    ).delete(synchronize_session=False)
    db.session.commit()
    return deleted
