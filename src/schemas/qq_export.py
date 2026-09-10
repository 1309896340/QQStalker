"""Schemas for JSON exports generated from NapCat by QQChatExporter.

The exporter supports many message-element variants.  Fields observed in the
first ten messages are modeled explicitly; ``extra='allow'`` retains fields
from newer exporter versions and message types that will be handled later.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExportSchema(BaseModel):
    """Base schema that preserves exporter fields not yet normalized."""

    model_config = ConfigDict(extra="allow")


class ExportMetadata(ExportSchema):
    name: str
    copyright: str
    version: str


class ChatInfo(ExportSchema):
    name: str
    type: str
    self_uid: str = Field(alias="selfUid")
    self_uin: str = Field(alias="selfUin")
    self_name: str = Field(alias="selfName")
    peer_uid: str = Field(alias="peerUid")
    avatar: str | None = None


class TimeRange(ExportSchema):
    start: str
    end: str
    duration_days: int = Field(alias="durationDays")


class SenderStatistic(ExportSchema):
    uid: str
    name: str
    message_count: int = Field(alias="messageCount")
    percentage: float


class ResourceStatistics(ExportSchema):
    by_type: dict[str, int] = Field(alias="byType")
    total: int
    total_size: int = Field(alias="totalSize")


class ExportStatistics(ExportSchema):
    total_messages: int = Field(alias="totalMessages")
    time_range: TimeRange = Field(alias="timeRange")
    message_types: dict[str, int] = Field(alias="messageTypes")
    senders: list[SenderStatistic]
    resources: ResourceStatistics


class ExportOptions(ExportSchema):
    encoding: str
    include_resource_links: bool = Field(alias="includeResourceLinks")
    include_system_messages: bool = Field(alias="includeSystemMessages")
    prefer_group_member_name: bool = Field(alias="preferGroupMemberName")
    time_format: str = Field(alias="timeFormat")


class ExportConfiguration(ExportSchema):
    included_fields: list[str] = Field(alias="includedFields")
    filters: dict[str, Any] = Field(default_factory=dict)
    options: ExportOptions


class MessageSender(ExportSchema):
    uid: str
    name: str
    uin: str | None = None
    nickname: str | None = None
    group_card: str | None = Field(default=None, alias="groupCard")


class MessageElement(ExportSchema):
    """An ordered rich-message element, such as text, reply, or system."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class MessageResource(ExportSchema):
    """A resource reference; image files remain in ``resources/images``."""

    type: str | None = None
    name: str | None = None
    path: str | None = None
    url: str | None = None


class MessageMention(ExportSchema):
    uid: str
    name: str
    type: str


class MessageContent(ExportSchema):
    text: str
    html: str
    elements: list[MessageElement] = Field(default_factory=list)
    resources: list[MessageResource] = Field(default_factory=list)
    mentions: list[MessageMention] = Field(default_factory=list)


class QQMessage(ExportSchema):
    id: str
    seq: str
    timestamp: int
    time: str
    sender: MessageSender
    type: str
    content: MessageContent
    recalled: bool
    system: bool


class QQChatExport(ExportSchema):
    metadata: ExportMetadata
    chat_info: ChatInfo = Field(alias="chatInfo")
    statistics: ExportStatistics
    messages: list[QQMessage]
    export_options: ExportConfiguration = Field(alias="exportOptions")
