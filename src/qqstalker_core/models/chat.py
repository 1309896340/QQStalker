"""Normalized SQLModel entities for imported QQ chat exports."""

from datetime import UTC, datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Column, DateTime, JSON, LargeBinary, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class ImportBatch(SQLModel, table=True):
    """One immutable import attempt for an exported JSON file."""

    __tablename__ = "import_batches"  # type: ignore
    __table_args__ = (
        UniqueConstraint("source_path", "source_sha256", name="uq_import_source_revision"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    chat_id: UUID | None = Field(default=None, foreign_key="chats.id", index=True)
    source_path: str = Field(index=True)
    source_sha256: str = Field(index=True, max_length=64)
    source_size_bytes: int
    exporter_name: str
    exporter_version: str
    started_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    chat: Optional["Chat"] = Relationship(back_populates="import_batches")


class Chat(SQLModel, table=True):
    """A QQ group, direct conversation, or other peer represented by an export."""

    __tablename__ = "chats"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    peer_uid: str = Field(unique=True, index=True)
    chat_type: str = Field(index=True)
    name: str
    avatar_url: str | None = None
    self_uid: str
    self_uin: str | None = None
    self_name: str | None = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    import_batches: list[ImportBatch] = Relationship(back_populates="chat")
    memberships: list["ChatMembership"] = Relationship(back_populates="chat")
    messages: list["Message"] = Relationship(back_populates="chat")


class Participant(SQLModel, table=True):
    """A distinct QQ account observed as a message sender or mentioned user."""

    __tablename__ = "participants"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    uid: str = Field(unique=True, index=True)
    uin: str | None = Field(default=None, index=True)
    display_name: str
    nickname: str | None = None
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    memberships: list["ChatMembership"] = Relationship(back_populates="participant")
    sent_messages: list["Message"] = Relationship(back_populates="sender")


class BinaryResource(SQLModel, table=True):
    """Deduplicated bytes for an image, audio file, video, or other attachment."""

    __tablename__ = "binary_resources"  # type: ignore

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    sha256: str = Field(unique=True, index=True, max_length=64)
    resource_type: str | None = Field(default=None, index=True)
    original_filename: str | None = None
    mime_type: str | None = None
    byte_size: int
    content: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    references: list["MessageResource"] = Relationship(back_populates="binary_resource")


class ChatMembership(SQLModel, table=True):
    """The chat-specific identity of a participant, including group card."""

    __tablename__ = "chat_memberships"  # type: ignore
    __table_args__ = (UniqueConstraint("chat_id", "participant_id", name="uq_chat_membership"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    chat_id: UUID = Field(foreign_key="chats.id", index=True)
    participant_id: UUID = Field(foreign_key="participants.id", index=True)
    group_card: str | None = None
    first_seen_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    last_seen_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))

    chat: Chat = Relationship(back_populates="memberships")
    participant: Participant = Relationship(back_populates="memberships")


class Message(SQLModel, table=True):
    """A normalized chat message while retaining the original rendered content."""

    __tablename__ = "messages"  # type: ignore
    __table_args__ = (UniqueConstraint("chat_id", "external_id", name="uq_chat_message"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    chat_id: UUID = Field(foreign_key="chats.id", index=True)
    sender_id: UUID | None = Field(default=None, foreign_key="participants.id", index=True)
    import_batch_id: UUID | None = Field(default=None, foreign_key="import_batches.id", index=True)
    primary_binary_resource_id: UUID | None = Field(
        default=None,
        foreign_key="binary_resources.id",
        index=True,
    )
    external_id: str
    sequence: str | None = None
    message_type: str = Field(index=True)
    sent_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False, index=True))
    source_timestamp_ms: int = Field(
        sa_column=Column(BigInteger, nullable=False, index=True)
    )
    text: str = ""
    html: str = ""
    recalled: bool = False
    system: bool = False
    raw_content_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    chat: Chat = Relationship(back_populates="messages")
    sender: Optional[Participant] = Relationship(back_populates="sent_messages")
    elements: list["MessageElement"] = Relationship(back_populates="message")
    resources: list["MessageResource"] = Relationship(back_populates="message")
    mentions: list["MessageMention"] = Relationship(back_populates="message")


class MessageElement(SQLModel, table=True):
    """One ordered rich-content element in a message."""

    __tablename__ = "message_elements"  # type: ignore
    __table_args__ = (UniqueConstraint("message_id", "position", name="uq_message_element_position"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    message_id: UUID = Field(foreign_key="messages.id", index=True)
    position: int
    element_type: str = Field(index=True)
    data_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    message: Message = Relationship(back_populates="elements")


class MessageResource(SQLModel, table=True):
    """A resource reference and its optional local file in resources/images."""

    __tablename__ = "message_resources"  # type: ignore
    __table_args__ = (UniqueConstraint("message_id", "position", name="uq_message_resource_position"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    message_id: UUID = Field(foreign_key="messages.id", index=True)
    binary_resource_id: UUID | None = Field(
        default=None,
        foreign_key="binary_resources.id",
        index=True,
    )
    position: int
    resource_type: str | None = Field(default=None, index=True)
    resource_name: str | None = None
    resource_path: str | None = None
    resource_url: str | None = None
    local_path: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    message: Message = Relationship(back_populates="resources")
    binary_resource: Optional[BinaryResource] = Relationship(back_populates="references")


class MessageMention(SQLModel, table=True):
    """A mention embedded in a message, linked when the participant is known."""

    __tablename__ = "message_mentions"  # type: ignore
    __table_args__ = (UniqueConstraint("message_id", "position", name="uq_message_mention_position"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    message_id: UUID = Field(foreign_key="messages.id", index=True)
    participant_id: UUID | None = Field(default=None, foreign_key="participants.id", index=True)
    position: int
    mentioned_uid: str = Field(index=True)
    display_name: str
    mention_type: str

    message: Message = Relationship(back_populates="mentions")
