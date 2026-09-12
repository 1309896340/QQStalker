"""The deliberately small OneBot 11 event subset accepted by this service."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OneBotMessageSegment(BaseModel):
    """One array-format OneBot message segment and its provider metadata."""

    model_config = ConfigDict(extra="allow")

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class GroupMessage(BaseModel):
    """An ordinary OneBot group message event."""

    model_config = ConfigDict(extra="allow")

    post_type: Literal["message"]
    message_type: Literal["group"]
    sub_type: Literal["normal"] = "normal"
    group_id: int
    message_id: int
    user_id: int
    time: int
    message: list[OneBotMessageSegment] = Field(default_factory=list)
    raw_message: str = ""
    sender: dict[str, Any] = Field(default_factory=dict)


class GroupRecall(BaseModel):
    """A OneBot group-recall notice referring to an existing message."""

    model_config = ConfigDict(extra="allow")

    post_type: Literal["notice"]
    notice_type: Literal["group_recall"]
    group_id: int
    message_id: int
    time: int
