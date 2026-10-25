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

# Nom de salle Socket.IO par comptoir sur le namespace /socket_app_counter.
# CONTRAT avec sockets.py (MESSAGING_ROOM_PREFIX, même valeur) : l'App
# Comptoir rejoint ``messaging_counter_<counter_id>`` via l'évènement
# ``join_messaging_room`` à chaque (re)connexion ; le service n'émet les
# signaux d'invalidation ciblés que vers ces salles.
MESSAGING_ROOM_PREFIX = "messaging_counter_"


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
            "initials": presence.staff.initials,
            "counter_id": presence.counter_id,
            "counter_name": presence.counter.name,
            "online": True,
        })
    return people


def _all_people(exclude_staff_id=None):
    """Annuaire complet : tous les équipiers enregistrés, en ligne ou non.

    Tous les ``Pharmacist`` sont listés, sans filtre ``is_active`` : la case
    « actif » de l'admin n'est pas alimentée en pratique (aucun équipier ne
    la possède sur les données réelles) et la login par initiales ne la
    consulte pas — la filtrer viderait l'annuaire. Les présences actives
    écrasent les entrées hors ligne avec le comptoir réel.
    """
    people = {
        pharmacist.id: {
            "staff_id": pharmacist.id,
            "name": pharmacist.name,
            "initials": pharmacist.initials,
            "counter_id": None,
            "counter_name": None,
            "online": False,
        }
        for pharmacist in Pharmacist.query.filter(
            Pharmacist.id != exclude_staff_id,
        ).all()
    }
    people.update(_active_people(exclude_staff_id=exclude_staff_id))
    return people


def _emit(event, data=None, rooms=None):
    """Émet un signal d'invalidation vers les App Comptoir.

    ``rooms`` est une liste d'ids de comptoir : l'évènement n'est émis que
    vers la salle ``messaging_counter_<id>`` de chacun (une émission par
    salle, doublons éliminés, ids None ignorés). Sans ``rooms``, diffusion à
    tout le namespace — comportement historique, conservé pour les signaux
    qui touchent tous les postes (présence, config, broadcast)."""
    if rooms is None:
        communikation("app_counter", data=data, event=event)
        return
    for counter_id in {cid for cid in rooms if cid is not None}:
        communikation(
            "app_counter", data=data, event=event,
            room=f"{MESSAGING_ROOM_PREFIX}{counter_id}",
        )


def heartbeat(counter_id, client_instance_id):
    require_enabled()
    counter, staff = resolve_identity(counter_id)
    client_instance_id = _valid_uuid(client_instance_id)
    current = now()
    presence = db.session.get(AppMessagingPresence, client_instance_id)
    created = presence is None
    # Évalué AVANT la mise à jour : un poste qui revient après expiration
    # doit re-signaliser sa présence pour que les autres rafraîchissent.
    expired = not created and presence.last_seen_at < _active_cutoff()
    changed = (
        created or expired
        or presence.staff_id != staff.id or presence.counter_id != counter.id
    )
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
        # Je suis expéditeur ou destinataire.
        or_(
            AppMessage.sender_staff_id == staff_id,
            AppMessage.recipients.any(
                AppMessageRecipient.recipient_staff_id == staff_id,
            ),
        ),
        # Fenêtre : journée locale, ou non-lu qui me concerne, ou envoyé
        # par moi et pas encore lu par un destinataire quelconque.
        or_(
            AppMessage.created_at >= _today_start(),
            AppMessage.recipients.any(
                and_(
                    AppMessageRecipient.recipient_staff_id == staff_id,
                    AppMessageRecipient.read_at.is_(None),
                ),
            ),
            and_(
                AppMessage.sender_staff_id == staff_id,
                AppMessage.recipients.any(
                    AppMessageRecipient.read_at.is_(None),
                ),
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
        "delivered_at": _iso(recipient.delivered_at),
        "read_at": _iso(recipient.read_at),
    }


def serialize_message(message, viewer_staff_id):
    recipients = [_recipient_payload(row) for row in message.recipients]
    viewer_recipient = next(
        (row for row in message.recipients if row.recipient_staff_id == viewer_staff_id),
        None,
    )
    direct_read_at = None
    direct_delivered_at = None
    if message.kind == "direct" and message.sender_staff_id == viewer_staff_id:
        if message.recipients:
            direct_read_at = _iso(message.recipients[0].read_at)
            direct_delivered_at = _iso(message.recipients[0].delivered_at)
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
        "delivered_at": direct_delivered_at,
        "read_at": direct_read_at,
        "is_unread": bool(viewer_recipient and not viewer_recipient.read_at),
    }


def _mark_delivered(staff):
    # delivered_at = l'app du destinataire est connectée : marqué dès que
    # son app interroge le serveur (état, fil). « Remis » ≠ « Lu ».
    rows = (
        AppMessageRecipient.query
        .filter(
            AppMessageRecipient.recipient_staff_id == staff.id,
            AppMessageRecipient.delivered_at.is_(None),
        )
        .options(joinedload(AppMessageRecipient.message))
        .all()
    )
    if not rows:
        return
    delivered_at = now()
    for row in rows:
        row.delivered_at = delivered_at
    db.session.commit()
    # Rafraîchissement ciblé : les comptoirs des expéditeurs concernés
    # voient leur « Remis » sans attendre. /!\ rooms vide -> NE PAS
    # appeler _emit : rooms=None diffuse à tout le namespace.
    rooms = {row.message.sender_counter_id for row in rows}
    rooms.discard(None)
    if rooms:
        _emit("messaging_changed", {"delivered": True}, rooms=rooms)


def state(counter_id):
    require_enabled()
    counter, staff = resolve_identity(counter_id)
    # L'app de cet équipier vient d'interroger le serveur : ses messages
    # destinés sont « remis ». heartbeat() délègue à state(), couvert aussi.
    _mark_delivered(staff)
    people = _all_people(exclude_staff_id=staff.id)
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
                    "initials": None,
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
                    "initials": None,
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
        "online_count": sum(1 for person in people.values() if person["online"]),
        "server_time": _iso(now()),
    }


def list_messages(counter_id, *, kind, peer_staff_id=None, before_id=None,
                  after_id=None, limit=PAGE_SIZE_DEFAULT):
    require_enabled()
    _counter, staff = resolve_identity(counter_id)
    _mark_delivered(staff)
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

    people = _all_people(exclude_staff_id=staff.id)
    if kind == "direct":
        try:
            recipient_id = int(payload.get("recipient_staff_id"))
        except (TypeError, ValueError):
            raise MessagingError("invalid_message", 400)
        # N'importe quel équipier enregistré, en ligne ou non ; jamais
        # soi-même (exclu de l'annuaire) ni un id inconnu.
        recipient = people.get(recipient_id)
        if recipient is None:
            raise MessagingError("invalid_message", 400)
        recipients = [recipient]
    else:
        scope = str(payload.get("scope") or "online")
        if scope not in {"all", "online"}:
            raise MessagingError("invalid_message", 400)
        if scope == "all":
            # Tous les équipiers enregistrés, y compris hors ligne ; les
            # entrées en ligne portent le comptoir réel de leur présence.
            recipients = list(people.values())
        else:
            recipients = [
                person for person in people.values() if person["online"]
            ]
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
    rooms = None
    if message.kind == "direct":
        # Message direct : seuls le comptoir de l'expéditeur et celui du
        # destinataire sont signalés. Un destinataire hors ligne n'a pas de
        # comptoir figé : il verra le non-lu à sa prochaine connexion.
        rooms = {counter.id}
        if recipient["counter_id"] is not None:
            rooms.add(recipient["counter_id"])
    # Broadcast : signal global — tout le namespace, comme avant.
    _emit("messaging_changed", {
        "message_id": message.id,
        "kind": message.kind,
        "sender_staff_id": staff.id,
        "sender_name": staff.name,
    }, rooms=rooms)
    result = serialize_message(message, staff.id)
    result["recipient_count"] = len(recipients)
    return result, True


def mark_read(counter_id, message_ids):
    require_enabled()
    counter, staff = resolve_identity(counter_id)
    if not isinstance(message_ids, list) or not message_ids or len(message_ids) > 100:
        raise MessagingError("invalid_message", 400)
    try:
        ids = {int(value) for value in message_ids}
    except (TypeError, ValueError):
        raise MessagingError("invalid_message", 400)
    rows = (
        AppMessageRecipient.query
        .filter(
            AppMessageRecipient.recipient_staff_id == staff.id,
            AppMessageRecipient.message_id.in_(ids),
            AppMessageRecipient.read_at.is_(None),
        )
        .options(joinedload(AppMessageRecipient.message))
        .all()
    )
    read_at = now()
    for row in rows:
        row.read_at = read_at
        # « Lu » implique « Remis » — même si l'app n'a jamais été marquée
        # destinataire d'un delivered_at avant (lecture directe du fil).
        row.delivered_at = row.delivered_at or read_at
    if rows:
        db.session.commit()
        # Ciblé : le comptoir du lecteur (son compteur de non-lus change)
        # et le comptoir de chaque expéditeur (sa pastille « lu » change).
        rooms = {counter.id}
        rooms.update(row.message.sender_counter_id for row in rows)
        _emit("messaging_changed", {"read": True}, rooms=rooms)
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
