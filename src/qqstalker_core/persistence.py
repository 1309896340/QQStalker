"""Shared PostgreSQL-safe normalization and message persistence helpers."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from src.qqstalker_core.models import (
    Chat,
    ChatMembership,
    Message,
    MessageElement,
    MessageMention,
    MessageResource,
    Participant,
)

LOGGER = logging.getLogger("qqstalker.persistence")
RESOURCE_SEGMENT_TYPES = frozenset({"file", "image", "record", "video"})


def sanitize_postgres_text(value: str | None, *, field_path: str) -> str | None:
    """Remove NUL bytes, which PostgreSQL cannot store in text or JSON."""

    del field_path
    return value.replace("\x00", "") if value is not None else None


def sanitize_postgres_json(value: Any, *, field_path: str) -> Any:
    """Recursively remove PostgreSQL-incompatible NUL bytes."""

    if isinstance(value, str):
        return sanitize_postgres_text(value, field_path=field_path)
    if isinstance(value, dict):
        return {
            sanitize_postgres_text(str(key), field_path=field_path) or "": sanitize_postgres_json(
                item, field_path=field_path
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_postgres_json(item, field_path=field_path) for item in value]
    return value


def get_or_create_participant(
    session: Session,
    sender_uid: str,
    *,
    uin: str | None,
    display_name: str,
    nickname: str | None,
    participant_ids: dict[str, UUID] | None = None,
) -> UUID:
    """Return a participant ID while keeping its current observable identity fresh."""

    normalized_uid = sanitize_postgres_text(sender_uid, field_path="participant.uid") or ""
    normalized_uin = sanitize_postgres_text(uin, field_path=f"participant[{normalized_uid}].uin")
    normalized_name = (
        sanitize_postgres_text(
            display_name,
            field_path=f"participant[{normalized_uid}].display_name",
        )
        or normalized_uid
    )
    normalized_nickname = sanitize_postgres_text(
        nickname,
        field_path=f"participant[{normalized_uid}].nickname",
    )
    if participant_ids is not None and normalized_uid in participant_ids:
        return participant_ids[normalized_uid]

    with session.no_autoflush:
        participant = session.exec(
            select(Participant).where(Participant.uid == normalized_uid)
        ).first()
    if participant is None:
        participant = Participant(
            uid=normalized_uid,
            uin=normalized_uin,
            display_name=normalized_name,
            nickname=normalized_nickname,
        )
        session.add(participant)
        session.flush()
    else:
        participant.uin = normalized_uin or participant.uin
        participant.display_name = normalized_name
        participant.nickname = normalized_nickname or participant.nickname
        participant.updated_at = datetime.now(UTC)

    if participant_ids is not None:
        participant_ids[normalized_uid] = participant.id
    return participant.id


def ensure_chat_membership(
    session: Session,
    *,
    chat_id: UUID,
    participant_id: UUID,
    group_card: str | None,
    membership_keys: set[tuple[UUID, UUID]] | None = None,
) -> None:
    """Create or refresh a participant's group-specific display card."""

    normalized_card = sanitize_postgres_text(
        group_card,
        field_path="chat_membership.group_card",
    )
    key = (chat_id, participant_id)
    if membership_keys is not None and key in membership_keys:
        return

    membership = session.exec(
        select(ChatMembership).where(
            ChatMembership.chat_id == chat_id,
            ChatMembership.participant_id == participant_id,
        )
    ).first()
    now = datetime.now(UTC)
    if membership is None:
        session.add(
            ChatMembership(
                chat_id=chat_id,
                participant_id=participant_id,
                group_card=normalized_card,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
    else:
        membership.group_card = normalized_card or membership.group_card
        membership.last_seen_at = now
    if membership_keys is not None:
        membership_keys.add(key)


def get_or_create_group_chat(session: Session, group_id: str) -> Chat:
    """Find the historical chat keyed by group number or create an explicit mapping."""

    chat = session.exec(select(Chat).where(Chat.peer_uid == group_id)).first()
    if chat is not None:
        return chat

    chat = Chat(
        peer_uid=group_id,
        chat_type="group",
        name=f"群 {group_id}",
        self_uid="napcat",
    )
    session.add(chat)
    session.flush()
    LOGGER.warning("未找到群号对应的历史会话，已新建会话：group_id=%s", group_id)
    return chat


def _event_text(message: list[Any], raw_message: str) -> str:
    text_parts: list[str] = []
    for segment in message:
        segment_type = str(getattr(segment, "type", ""))
        data = getattr(segment, "data", {})
        if segment_type == "text" and isinstance(data, dict):
            text_parts.append(str(data.get("text", "")))
    return "".join(text_parts) or raw_message


def _segment_data(segment: Any, position: int) -> dict[str, Any]:
    data = getattr(segment, "data", {})
    if not isinstance(data, dict):
        raise ValueError(f"message[{position}].data 必须是对象")
    return sanitize_postgres_json(data, field_path=f"message[{position}].data")


def persist_group_message(session: Session, event: Any) -> bool:
    """Persist one OneBot group message and return ``False`` for a replay.

    The caller owns the outer transaction. The message and its child rows are
    flushed in a savepoint so a unique-key race is treated as a replay.
    """

    group_id = str(event.group_id)
    message_id = str(event.message_id)
    sender_data = event.sender
    if not isinstance(sender_data, dict):
        raise ValueError("sender 必须是对象")

    chat = get_or_create_group_chat(session, group_id)
    existing = session.exec(
        select(Message).where(
            Message.chat_id == chat.id,
            Message.external_id == message_id,
        )
    ).first()
    if existing is not None:
        return False

    sender_uid = str(event.user_id)
    sender_name = str(sender_data.get("card") or sender_data.get("nickname") or sender_uid)
    sender_id = get_or_create_participant(
        session,
        sender_uid,
        uin=sender_uid,
        display_name=sender_name,
        nickname=str(sender_data.get("nickname", "")) or None,
    )
    ensure_chat_membership(
        session,
        chat_id=chat.id,
        participant_id=sender_id,
        group_card=str(sender_data.get("card", "")) or None,
    )

    segments = list(event.message)
    resources: list[dict[str, Any]] = []
    for position, segment in enumerate(segments):
        segment_type = str(getattr(segment, "type", ""))
        if segment_type in RESOURCE_SEGMENT_TYPES:
            resources.append(
                {
                    "position": position,
                    "type": segment_type,
                    "data": _segment_data(segment, position),
                }
            )
    raw_content = sanitize_postgres_json(
        {
            "elements": [
                {
                    "type": str(getattr(segment, "type", "")),
                    "data": _segment_data(segment, position),
                }
                for position, segment in enumerate(segments)
            ],
            "resources": resources,
        },
        field_path="message",
    )
    db_message = Message(
        chat_id=chat.id,
        sender_id=sender_id,
        external_id=message_id,
        message_type="group",
        sent_at=datetime.fromtimestamp(int(event.time), UTC),
        source_timestamp_ms=int(event.time) * 1000,
        text=sanitize_postgres_text(
            _event_text(segments, str(event.raw_message)),
            field_path="message.text",
        )
        or "",
        raw_content_json=raw_content,
    )

    try:
        with session.begin_nested():
            session.add(db_message)
            for position, segment in enumerate(segments):
                segment_type = str(getattr(segment, "type", ""))
                data = _segment_data(segment, position)
                session.add(
                    MessageElement(
                        message_id=db_message.id,
                        position=position,
                        element_type=segment_type or "unknown",
                        data_json=data,
                    )
                )
                if segment_type == "at":
                    mentioned_uid = str(data.get("qq", ""))
                    is_all = mentioned_uid == "all"
                    mentioned_id = None
                    display_name = str(data.get("name") or ("全体成员" if is_all else mentioned_uid))
                    if mentioned_uid and not is_all:
                        mentioned_id = get_or_create_participant(
                            session,
                            mentioned_uid,
                            uin=mentioned_uid,
                            display_name=display_name,
                            nickname=None,
                        )
                    session.add(
                        MessageMention(
                            message_id=db_message.id,
                            participant_id=mentioned_id,
                            position=position,
                            mentioned_uid=mentioned_uid or "all",
                            display_name=display_name,
                            mention_type="at_all" if is_all else "at",
                        )
                    )
                if segment_type in RESOURCE_SEGMENT_TYPES:
                    session.add(
                        MessageResource(
                            message_id=db_message.id,
                            position=position,
                            resource_type=segment_type,
                            resource_name=str(
                                data.get("name")
                                or data.get("file")
                                or data.get("filename")
                                or ""
                            )
                            or None,
                            resource_path=str(data.get("path") or data.get("file_id") or "") or None,
                            resource_url=str(data.get("url") or "") or None,
                            metadata_json=data,
                        )
                    )
            session.flush()
    except IntegrityError:
        LOGGER.info("重复实时消息已跳过：group_id=%s message_id=%s", group_id, message_id)
        return False
    return True


def mark_group_message_recalled(session: Session, group_id: int, message_id: int) -> bool:
    """Mark a known message recalled; missing targets are intentionally ignored."""

    chat = session.exec(select(Chat).where(Chat.peer_uid == str(group_id))).first()
    if chat is None:
        return False
    message = session.exec(
        select(Message).where(
            Message.chat_id == chat.id,
            Message.external_id == str(message_id),
        )
    ).first()
    if message is None:
        return False
    message.recalled = True
    return True
