"""Deterministic, bounded conversation context for member portrait prompts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re


MESSAGE_BLOCK_PATTERN = re.compile(
    r"(?ms)^## (?P<header>[^\n]+)\n\n> \*\*(?P<member>.+?)\*\*\n>\n(?P<body>.*?)(?=^## |\Z)"
)
TIMESTAMP_PATTERN = re.compile(r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
PLACEHOLDER_PATTERN = re.compile(r"(?:\[[^\]]*(?:图片|表情)[^\]]*\]|[\U0001F300-\U0001FAFF])")


@dataclass(frozen=True)
class TranscriptMessage:
    """One human message as rendered by the Markdown exporter."""

    index: int
    timestamp: datetime
    member: str
    content: str
    raw: str


def parse_messages(transcript: str, *, excluded_members: frozenset[str]) -> tuple[TranscriptMessage, ...]:
    """Parse human messages in export order, excluding system/service senders."""

    parsed: list[TranscriptMessage] = []
    for match in MESSAGE_BLOCK_PATTERN.finditer(transcript):
        member = match.group("member").strip()
        timestamp_match = TIMESTAMP_PATTERN.match(match.group("header"))
        if not member or member in excluded_members or timestamp_match is None:
            continue
        timestamp = datetime.strptime(timestamp_match.group("timestamp"), "%Y-%m-%d %H:%M:%S")
        raw = match.group(0).strip()
        body = _unquote(match.group("body"))
        parsed.append(
            TranscriptMessage(
                index=len(parsed), timestamp=timestamp, member=member, content=body, raw=raw
            )
        )
    return tuple(parsed)


def _unquote(body: str) -> str:
    return "\n".join(line[2:] if line.startswith("> ") else line.lstrip(">") for line in body.splitlines()).strip()


def group_messages(messages: tuple[TranscriptMessage, ...]) -> dict[str, list[TranscriptMessage]]:
    grouped: dict[str, list[TranscriptMessage]] = {}
    for message in messages:
        grouped.setdefault(message.member, []).append(message)
    return grouped


def has_textual_content(message: TranscriptMessage) -> bool:
    """Reject empty media placeholders as representative-message centers."""

    return bool(PLACEHOLDER_PATTERN.sub("", message.content).strip(" \t\n\r，。！？!?"))


def has_interaction_signal(message: TranscriptMessage) -> bool:
    """Recognize conservative, directly visible interaction cues."""

    content = message.content
    return "@" in content or "？" in content or "?" in content or "回复" in content or "引用" in content


def choose_representatives(
    messages: tuple[TranscriptMessage, ...], *, maximum: int
) -> tuple[TranscriptMessage, ...]:
    """Prefer direct cues then fill remaining slots across the member's timeline."""

    candidates = [message for message in messages if has_textual_content(message)]
    if not candidates or maximum <= 0:
        return ()
    chosen = [message for message in candidates if has_interaction_signal(message)][:maximum]
    remaining = [message for message in candidates if message not in chosen]
    slots = maximum - len(chosen)
    if slots > 0 and remaining:
        if len(remaining) <= slots:
            chosen.extend(remaining)
        else:
            positions = [round(index * (len(remaining) - 1) / (slots - 1)) for index in range(slots)] if slots > 1 else [len(remaining) // 2]
            chosen.extend(remaining[position] for position in positions)
    return tuple(sorted({message.index: message for message in chosen}.values(), key=lambda message: message.index))


def merged_window_messages(
    messages: tuple[TranscriptMessage, ...],
    centers: tuple[TranscriptMessage, ...],
    *,
    before: int,
    after: int,
) -> tuple[TranscriptMessage, ...]:
    """Return the merged, de-duplicated chronological windows around centers."""

    selected: dict[int, TranscriptMessage] = {}
    for center in centers:
        start = max(0, center.index - before)
        stop = min(len(messages), center.index + after + 1)
        for message in messages[start:stop]:
            selected[message.index] = message
    return tuple(selected[index] for index in sorted(selected))


def serialize_context(
    messages: tuple[TranscriptMessage, ...],
    centers: tuple[TranscriptMessage, ...],
    *,
    maximum_characters: int,
) -> str:
    """Render one member's context once, retaining headers when content is clipped."""

    center_ids = {message.index for message in centers}
    lines: list[str] = []
    for message in messages:
        marker = "（代表性发言）" if message.index in center_ids else ""
        prefix = f"[{message.index + 1} | {message.timestamp:%Y-%m-%d %H:%M:%S} | {message.member}]{marker} "
        lines.append(prefix + message.content)
    rendered = "\n".join(lines)
    if len(rendered) <= maximum_characters:
        return rendered
    if maximum_characters < 24:
        return rendered[:maximum_characters]
    return rendered[: maximum_characters - 10].rstrip() + "…（内容已截断）"


def build_member_contexts(
    messages: tuple[TranscriptMessage, ...],
    members: tuple[str, ...],
    *,
    before: int,
    after: int,
    maximum_windows: int,
    maximum_characters: int,
) -> dict[str, str]:
    """Build per-member bounded contexts from one shared chronological transcript."""

    grouped = group_messages(messages)
    contexts: dict[str, str] = {}
    for member in members:
        centers = choose_representatives(tuple(grouped.get(member, ())), maximum=maximum_windows)
        windows = merged_window_messages(messages, centers, before=before, after=after)
        contexts[member] = serialize_context(windows, centers, maximum_characters=maximum_characters)
    return contexts
