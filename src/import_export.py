"""Import a QQChatExporter JSON document and referenced binary resources into PostgreSQL."""

from __future__ import annotations

import argparse
import hashlib
import mimetypes
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import orjson
from sqlalchemy import Engine, URL
from sqlmodel import Session, SQLModel, create_engine, select

from models import (
    BinaryResource,
    Chat,
    ChatMembership,
    ImportBatch,
    Message,
    MessageElement,
    MessageMention,
    MessageResource,
    Participant,
)
from parse_export import default_images_dir
from schemas.qq_export import MessageResource as ExportedResource
from schemas.qq_export import QQChatExport, QQMessage

BATCH_SIZE = 500


def database_url_from_environment() -> str:
    """Build a psycopg URL from DATABASE_URL or the Compose environment variables."""

    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url

    database = os.getenv("POSTGRES_DB", "qqstalker")
    user = os.getenv("POSTGRES_USER", "qqstalker")
    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("Set DATABASE_URL or POSTGRES_PASSWORD before importing.")

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    return str(
        URL.create(
            "postgresql+psycopg",
            username=user,
            password=password,
            host=host,
            port=int(port),
            database=database,
        )
    )


def create_database_engine() -> Engine:
    return create_engine(database_url_from_environment(), pool_pre_ping=True)


def resolve_resource_path(
    resource: ExportedResource,
    *,
    export_dir: Path,
    images_dir: Path,
) -> Path | None:
    """Resolve a resource link while preventing paths from escaping the export tree."""

    export_root = export_dir.resolve()
    resources_root = (export_root / "resources").resolve()
    images_root = images_dir.resolve()
    candidates: list[Path] = []

    for reference in (resource.local_path, resource.url):
        if not reference:
            continue
        normalized = Path(reference.replace("\\", "/"))
        if normalized.is_absolute():
            continue
        candidates.extend((resources_root / normalized, export_root / normalized))

    filename = resource.filename or resource.name
    if filename:
        candidates.append(images_root / Path(filename).name)

    for candidate in candidates:
        resolved = candidate.resolve()
        if (
            resolved.is_file()
            and (resolved.is_relative_to(export_root) or resolved.is_relative_to(images_root))
        ):
            return resolved
    return None


def get_or_create_participant(
    session: Session,
    sender_uid: str,
    *,
    uin: str | None,
    display_name: str,
    nickname: str | None,
    participant_ids: dict[str, UUID],
) -> UUID:
    if sender_uid in participant_ids:
        return participant_ids[sender_uid]

    participant = session.exec(select(Participant).where(Participant.uid == sender_uid)).first()
    if participant is None:
        participant = Participant(
            uid=sender_uid,
            uin=uin,
            display_name=display_name,
            nickname=nickname,
        )
        session.add(participant)
    else:
        participant.uin = uin or participant.uin
        participant.display_name = display_name
        participant.nickname = nickname or participant.nickname
        participant.updated_at = datetime.now(UTC)

    participant_ids[sender_uid] = participant.id
    return participant.id


def get_or_create_binary_resource(
    session: Session,
    *,
    file_path: Path,
    resource: ExportedResource,
    binary_ids: dict[str, UUID],
    binary_file_ids: dict[Path, UUID],
) -> UUID:
    resolved_path = file_path.resolve()
    if resolved_path in binary_file_ids:
        return binary_file_ids[resolved_path]

    content = file_path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if checksum in binary_ids:
        return binary_ids[checksum]

    binary_resource = session.exec(
        select(BinaryResource).where(BinaryResource.sha256 == checksum)
    ).first()
    if binary_resource is None:
        binary_resource = BinaryResource(
            sha256=checksum,
            resource_type=resource.type,
            original_filename=resource.filename or resource.name or file_path.name,
            mime_type=mimetypes.guess_type(file_path.name)[0],
            byte_size=len(content),
            content=content,
        )
        session.add(binary_resource)

    binary_ids[checksum] = binary_resource.id
    binary_file_ids[resolved_path] = binary_resource.id
    return binary_resource.id


def ensure_chat_membership(
    session: Session,
    *,
    chat_id: UUID,
    participant_id: UUID,
    group_card: str | None,
    membership_keys: set[tuple[UUID, UUID]],
) -> None:
    key = (chat_id, participant_id)
    if key in membership_keys:
        return

    membership = session.exec(
        select(ChatMembership).where(
            ChatMembership.chat_id == chat_id,
            ChatMembership.participant_id == participant_id,
        )
    ).first()
    if membership is None:
        session.add(
            ChatMembership(
                chat_id=chat_id,
                participant_id=participant_id,
                group_card=group_card,
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
        )
    else:
        membership.group_card = group_card or membership.group_card
        membership.last_seen_at = datetime.now(UTC)
    membership_keys.add(key)


def synchronize_message(
    session: Session,
    message: QQMessage,
    *,
    chat_id: UUID,
    import_batch_id: UUID,
    export_dir: Path,
    images_dir: Path,
    participant_ids: dict[str, UUID],
    binary_ids: dict[str, UUID],
    binary_file_ids: dict[Path, UUID],
    membership_keys: set[tuple[UUID, UUID]],
) -> None:
    sender_id = get_or_create_participant(
        session,
        message.sender.uid,
        uin=message.sender.uin,
        display_name=message.sender.name,
        nickname=message.sender.nickname,
        participant_ids=participant_ids,
    )
    ensure_chat_membership(
        session,
        chat_id=chat_id,
        participant_id=sender_id,
        group_card=message.sender.group_card,
        membership_keys=membership_keys,
    )
    sent_at = datetime.fromisoformat(message.time.replace("Z", "+00:00"))
    db_message = Message(
        chat_id=chat_id,
        sender_id=sender_id,
        import_batch_id=import_batch_id,
        external_id=message.id,
        sequence=message.seq,
        message_type=message.type,
        sent_at=sent_at,
        source_timestamp_ms=message.timestamp,
        text=message.content.text,
        html=message.content.html,
        recalled=message.recalled,
        system=message.system,
        raw_content_json=message.content.model_dump(by_alias=True, mode="json"),
    )
    session.add(db_message)

    for position, element in enumerate(message.content.elements):
        session.add(
            MessageElement(
                message_id=db_message.id,
                position=position,
                element_type=element.type,
                data_json=element.data,
            )
        )

    for position, mention in enumerate(message.content.mentions):
        mentioned_participant_id = get_or_create_participant(
            session,
            mention.uid,
            uin=None,
            display_name=mention.name,
            nickname=None,
            participant_ids=participant_ids,
        )
        session.add(
            MessageMention(
                message_id=db_message.id,
                participant_id=mentioned_participant_id,
                position=position,
                mentioned_uid=mention.uid,
                display_name=mention.name,
                mention_type=mention.type,
            )
        )

    for position, resource in enumerate(message.content.resources):
        resource_path = resolve_resource_path(
            resource,
            export_dir=export_dir,
            images_dir=images_dir,
        )
        binary_resource_id = (
            get_or_create_binary_resource(
                session,
                file_path=resource_path,
                resource=resource,
                binary_ids=binary_ids,
                binary_file_ids=binary_file_ids,
            )
            if resource_path is not None
            else None
        )
        if resource.type == "image" and db_message.primary_binary_resource_id is None:
            db_message.primary_binary_resource_id = binary_resource_id
        session.add(
            MessageResource(
                message_id=db_message.id,
                binary_resource_id=binary_resource_id,
                position=position,
                resource_type=resource.type,
                resource_name=resource.filename or resource.name,
                resource_path=resource.local_path or resource.path,
                resource_url=resource.url,
                local_path=str(resource_path) if resource_path else None,
                metadata_json=resource.model_dump(by_alias=True, mode="json"),
            )
        )


def synchronize_export(json_path: Path, images_dir: Path, engine: Engine) -> tuple[int, int]:
    """Synchronize every message and linked local resource from one JSON export."""

    if not json_path.is_file():
        raise FileNotFoundError(f"JSON export does not exist: {json_path}")
    if not images_dir.is_dir():
        raise FileNotFoundError(f"Image resources directory does not exist: {images_dir}")

    source_bytes = json_path.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    export = QQChatExport.model_validate(orjson.loads(source_bytes))
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session, session.begin():
        chat = session.exec(select(Chat).where(Chat.peer_uid == export.chat_info.peer_uid)).first()
        if chat is None:
            chat = Chat(
                peer_uid=export.chat_info.peer_uid,
                chat_type=export.chat_info.type,
                name=export.chat_info.name,
                avatar_url=export.chat_info.avatar,
                self_uid=export.chat_info.self_uid,
                self_uin=export.chat_info.self_uin,
                self_name=export.chat_info.self_name,
            )
            session.add(chat)
            session.flush()
        else:
            chat.chat_type = export.chat_info.type
            chat.name = export.chat_info.name
            chat.avatar_url = export.chat_info.avatar
            chat.self_uid = export.chat_info.self_uid
            chat.self_uin = export.chat_info.self_uin
            chat.self_name = export.chat_info.self_name
            chat.updated_at = datetime.now(UTC)

        existing_batch = session.exec(
            select(ImportBatch).where(
                ImportBatch.source_path == str(json_path.resolve()),
                ImportBatch.source_sha256 == source_sha256,
            )
        ).first()
        if existing_batch and existing_batch.completed_at:
            return 0, 0

        batch = existing_batch or ImportBatch(
            chat_id=chat.id,
            source_path=str(json_path.resolve()),
            source_sha256=source_sha256,
            source_size_bytes=len(source_bytes),
            exporter_name=export.metadata.name,
            exporter_version=export.metadata.version,
            metadata_json=export.model_dump(by_alias=True, mode="json", exclude={"messages"}),
        )
        session.add(batch)
        session.flush()

        existing_message_ids = set(
            session.exec(select(Message.external_id).where(Message.chat_id == chat.id)).all()
        )
        participant_ids: dict[str, UUID] = {}
        binary_ids: dict[str, UUID] = {}
        binary_file_ids: dict[Path, UUID] = {}
        membership_keys: set[tuple[UUID, UUID]] = set()
        imported_messages = 0
        skipped_messages = 0

        for message in export.messages:
            if message.id in existing_message_ids:
                skipped_messages += 1
                continue
            synchronize_message(
                session,
                message,
                chat_id=chat.id,
                import_batch_id=batch.id,
                export_dir=json_path.parent,
                images_dir=images_dir,
                participant_ids=participant_ids,
                binary_ids=binary_ids,
                binary_file_ids=binary_file_ids,
                membership_keys=membership_keys,
            )
            imported_messages += 1
            if imported_messages % BATCH_SIZE == 0:
                session.flush()
                session.expunge_all()

        batch = session.get(ImportBatch, batch.id)
        assert batch is not None
        batch.completed_at = datetime.now(UTC)
        return imported_messages, skipped_messages


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", type=Path, help="Path to a QQChatExporter JSON file")
    parser.add_argument(
        "--images-dir",
        type=Path,
        help="Image directory (defaults to <json parent>/resources/images)",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    images_dir = args.images_dir or default_images_dir(args.json_path)
    imported, skipped = synchronize_export(args.json_path, images_dir, create_database_engine())
    print(f"Import complete: {imported} messages inserted, {skipped} messages already present.")


if __name__ == "__main__":
    main()
