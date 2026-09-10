"""Export QQ chat messages from PostgreSQL to a Markdown transcript."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, select

from src.import_export import create_database_engine
from src.models import Chat, ChatMembership, Message, Participant

DEFAULT_TIMEZONE = "Asia/Shanghai"


def parse_date(value: str) -> date:
    """Parse one ISO-8601 calendar date for the command-line interface."""

    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("日期必须使用 YYYY-MM-DD 格式") from error


def timestamp_bounds(day: date, timezone: ZoneInfo) -> tuple[int, int]:
    """Return inclusive/exclusive epoch-millisecond bounds for a local calendar day."""

    start = datetime.combine(day, time.min, tzinfo=timezone)
    end = datetime.combine(day.fromordinal(day.toordinal() + 1), time.min, tzinfo=timezone)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def quote_markdown(text: str) -> str:
    """Render message text inside a Markdown blockquote without interpreting images."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return "> [无文本内容]"
    return "\n".join(f"> {line}" if line else ">" for line in normalized.split("\n"))


def image_placeholder_count(raw_content: dict[str, Any]) -> int:
    """Count image resources embedded in the original message payload."""

    resources = raw_content.get("resources", [])
    if not isinstance(resources, list):
        return 0
    return sum(
        1
        for resource in resources
        if isinstance(resource, dict) and resource.get("type") == "image"
    )


def local_message_time(message: Message, timezone: ZoneInfo) -> datetime:
    """Interpret the legacy PostgreSQL timestamp as UTC, then convert for display."""

    sent_at = message.sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)
    return sent_at.astimezone(timezone)


def render_transcript(
    rows: list[tuple[Message, str | None, str | None, str]],
    *,
    start_date: date,
    end_date: date,
    timezone: ZoneInfo,
) -> str:
    """Build a human-readable transcript with one familiar chat bubble per message."""

    lines = [
        "# QQ 消息记录",
        "",
        f"- 日期范围：{start_date.isoformat()} 至 {end_date.isoformat()}（含首尾）",
        f"- 显示时区：{timezone.key}",
        f"- 消息数量：{len(rows)}",
        "",
        "---",
    ]

    for message, display_name, group_card, chat_name in rows:
        sent_at = local_message_time(message, timezone)
        sender_name = group_card or display_name or "未知成员"
        lines.extend(
            (
                "",
                f"## {sent_at:%Y-%m-%d %H:%M:%S} · {chat_name}",
                "",
                f"> **{sender_name}**",
                ">",
            )
        )
        if message.recalled:
            lines.append("> *[消息已撤回]*")
        else:
            lines.append(quote_markdown(message.text))

        image_count = image_placeholder_count(message.raw_content_json)
        if image_count:
            placeholder = "[图片]" if image_count == 1 else f"[图片 × {image_count}]"
            lines.extend((">", f"> *{placeholder}*"))

    return "\n".join(lines) + "\n"


def export_markdown(
    start_date: date,
    end_date: date,
    output_dir: Path,
    *,
    timezone: ZoneInfo,
) -> Path:
    """Query all chats in a local date range and write a single Markdown transcript."""

    start_timestamp, _ = timestamp_bounds(start_date, timezone)
    _, end_timestamp = timestamp_bounds(end_date, timezone)
    # SQLModel exposes these SQLAlchemy tables dynamically; its type stubs omit them.
    message_table = Message.__table__  # type: ignore
    participant_table = Participant.__table__  # type: ignore
    membership_table = ChatMembership.__table__  # type: ignore
    chat_table = Chat.__table__  # type: ignore
    statement = (
        select(
            Message,
            participant_table.c.display_name,
            membership_table.c.group_card,
            chat_table.c.name,
        )
        .join(Chat, message_table.c.chat_id == chat_table.c.id)
        .join(Participant, message_table.c.sender_id == participant_table.c.id, isouter=True)
        .join(
            ChatMembership,
            (membership_table.c.chat_id == message_table.c.chat_id)
            & (membership_table.c.participant_id == message_table.c.sender_id),
            isouter=True,
        )
        .where(
            message_table.c.source_timestamp_ms >= start_timestamp,
            message_table.c.source_timestamp_ms < end_timestamp,
        )
        .order_by(
            message_table.c.source_timestamp_ms,
            message_table.c.sequence,
            message_table.c.id,
        )
    )

    with Session(create_database_engine()) as session:
        result_rows = session.exec(statement).all()
    rows: list[tuple[Message, str | None, str | None, str]] = [
        (message, display_name, group_card, chat_name)
        for message, display_name, group_card, chat_name in result_rows
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone).strftime("%Y%m%d%H%M%S")
    output_path = output_dir / f"{generated_at}_消息记录.md"
    output_path.write_text(
        render_transcript(
            rows,
            start_date=start_date,
            end_date=end_date,
            timezone=timezone,
        ),
        encoding="utf-8",
    )
    return output_path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("start_date", type=parse_date, help="起始日期，格式 YYYY-MM-DD")
    parser.add_argument("output_dir", type=Path, help="Markdown 输出目录")
    parser.add_argument(
        "--end-date",
        type=parse_date,
        help="结束日期，格式 YYYY-MM-DD；省略时仅导出起始日期",
    )
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"用于日期筛选和显示的 IANA 时区（默认：{DEFAULT_TIMEZONE}）",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    end_date = args.end_date or args.start_date
    if end_date < args.start_date:
        raise SystemExit("--end-date 不能早于 start_date")
    try:
        timezone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError as error:
        raise SystemExit(f"未知时区：{args.timezone}") from error

    output_path = export_markdown(
        args.start_date,
        end_date,
        args.output_dir,
        timezone=timezone,
    )
    print(f"已导出消息记录：{output_path.resolve()}")


if __name__ == "__main__":
    main()
