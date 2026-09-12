"""Generate group-member portrait PNGs from PostgreSQL messages in one command."""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src import analyze_transcript, export_markdown, render_html_png


def environment_positive_integer(name: str, default: int) -> int:
    """Read a positive render setting after the selected environment file is loaded."""

    try:
        return render_html_png.positive_integer(os.getenv(name, str(default)))
    except argparse.ArgumentTypeError as error:
        raise RuntimeError(f"{name} {error}") from error


def generate_portrait(
    start_date: date,
    end_date: date,
    chat_name: str,
    output_dir: Path,
    *,
    timezone: ZoneInfo,
    env_file: Path,
    top_members: int | None,
    min_message_count: int,
    quote_count: int,
    dpi: int | None,
    width_millimeters: float,
    max_height_pixels: int | None,
    stitch_horizontal: bool,
    stitch_count: int,
) -> list[Path]:
    """Export, analyze, and render portraits while keeping intermediate files temporary."""

    analyze_transcript.load_dotenv(env_file)

    with tempfile.TemporaryDirectory(prefix="qqstalker-portrait-") as temporary_directory:
        temporary_dir = Path(temporary_directory)
        markdown_path = export_markdown.export_markdown(
            start_date,
            end_date,
            chat_name,
            temporary_dir,
            timezone=timezone,
        )
        model = analyze_transcript.required_setting("LLM_MODEL")
        resolved_dpi = (
            dpi
            if dpi is not None
            else environment_positive_integer(
                render_html_png.PNG_DPI_ENV,
                render_html_png.DEFAULT_DPI,
            )
        )
        resolved_max_height_pixels = (
            max_height_pixels
            if max_height_pixels is not None
            else environment_positive_integer(
                render_html_png.PNG_MAX_HEIGHT_PIXELS_ENV,
                render_html_png.DEFAULT_MAX_HEIGHT_PIXELS,
            )
        )
        analysis = analyze_transcript.analyze_all_members(
            markdown_path.read_text(encoding="utf-8"),
            base_url=analyze_transcript.required_setting("LLM_BASE_URL"),
            model=model,
            api_key=analyze_transcript.required_setting("LLM_API_KEY"),
            max_tokens=analyze_transcript.positive_integer_setting(
                "LLM_MAX_TOKENS",
                analyze_transcript.DEFAULT_MAX_TOKENS,
            ),
            timeout_seconds=float(
                analyze_transcript.positive_integer_setting(
                    "LLM_TIMEOUT_SECONDS",
                    int(analyze_transcript.DEFAULT_TIMEOUT_SECONDS),
                )
            ),
            members_per_request=analyze_transcript.positive_integer_setting(
                "LLM_MEMBERS_PER_REQUEST",
                analyze_transcript.DEFAULT_MEMBERS_PER_REQUEST,
            ),
            max_input_characters=analyze_transcript.positive_integer_setting(
                "LLM_MAX_INPUT_CHARACTERS",
                analyze_transcript.DEFAULT_MAX_INPUT_CHARACTERS,
            ),
            top_members=top_members,
            min_message_count=min_message_count,
            quote_count=quote_count,
        )
        generated_at = datetime.now()
        html_path = analyze_transcript.resolve_output_path(
            output_dir,
            generated_at=generated_at,
            chat_name=chat_name,
        )
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(
            analyze_transcript.render_html(analysis),
            encoding="utf-8",
        )
        output_path = render_html_png.create_output_path(
            output_dir,
            generated_at=generated_at,
            chat_name=chat_name,
        )
        return render_html_png.render_html_to_pngs(
            html_path,
            output_path,
            dpi=resolved_dpi,
            width_millimeters=width_millimeters,
            max_height_pixels=resolved_max_height_pixels,
            stitch_horizontal=stitch_horizontal,
            stitch_count=stitch_count,
        )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "start_date",
        type=export_markdown.parse_date,
        help="起始日期，格式 YYYY-MM-DD",
    )
    parser.add_argument("chat_name", help="必填，按名称筛选的群聊")
    parser.add_argument("output_dir", type=Path, help="最终 PNG 输出目录")
    parser.add_argument(
        "--end-date",
        type=export_markdown.parse_date,
        help="结束日期，格式 YYYY-MM-DD；省略时仅处理起始日期",
    )
    parser.add_argument(
        "--timezone",
        default=export_markdown.DEFAULT_TIMEZONE,
        help=(
            "用于消息日期筛选和显示的 IANA 时区"
            f"（默认：{export_markdown.DEFAULT_TIMEZONE}）"
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="包含数据库与 LLM 配置的文件（默认：.env）",
    )
    parser.add_argument(
        "--top-members",
        type=analyze_transcript.positive_integer,
        help="仅分析发言数量最多的前 N 位群员",
    )
    parser.add_argument(
        "--min-message-count",
        type=analyze_transcript.nonnegative_integer,
        default=0,
        help="忽略发言数量少于 N 条的群员（默认：0）",
    )
    parser.add_argument(
        "--quote-count",
        type=analyze_transcript.positive_integer,
        default=analyze_transcript.DEFAULT_FEATURED_QUOTE_COUNT,
        help=(
            "语录精选的目标条数"
            f"（默认：{analyze_transcript.DEFAULT_FEATURED_QUOTE_COUNT}）"
        ),
    )
    parser.add_argument(
        "--dpi",
        type=render_html_png.positive_integer,
        help=(
            f"目标 PNG DPI（默认：环境变量 {render_html_png.PNG_DPI_ENV} "
            f"或 {render_html_png.DEFAULT_DPI}）"
        ),
    )
    parser.add_argument(
        "--width-mm",
        type=render_html_png.positive_float,
        default=render_html_png.DEFAULT_WIDTH_MILLIMETERS,
        help=(
            "渲染宽度，单位毫米"
            f"（默认：{render_html_png.DEFAULT_WIDTH_MILLIMETERS:g}）"
        ),
    )
    parser.add_argument(
        "--max-height-px",
        type=render_html_png.positive_integer,
        help=(
            "单张图片最大物理像素高度；超出时自动分片"
            f"（默认：环境变量 {render_html_png.PNG_MAX_HEIGHT_PIXELS_ENV} "
            f"或 {render_html_png.DEFAULT_MAX_HEIGHT_PIXELS}）"
        ),
    )
    parser.add_argument(
        "--stitch-horizontal",
        action="store_true",
        help="将连续的垂直分片横向拼接为最终 PNG",
    )
    parser.add_argument(
        "--stitch-count",
        type=render_html_png.positive_integer,
        default=4,
        help="每张拼接图片包含的分片数量（仅 --stitch-horizontal 生效，默认：4）",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    end_date = args.end_date or args.start_date
    if end_date < args.start_date:
        raise SystemExit("--end-date 不能早于 start_date")
    try:
        timezone = ZoneInfo(args.timezone)
        paths = generate_portrait(
            args.start_date,
            end_date,
            args.chat_name,
            args.output_dir,
            timezone=timezone,
            env_file=args.env_file,
            top_members=args.top_members,
            min_message_count=args.min_message_count,
            quote_count=args.quote_count,
            dpi=args.dpi,
            width_millimeters=args.width_mm,
            max_height_pixels=args.max_height_px,
            stitch_horizontal=args.stitch_horizontal,
            stitch_count=args.stitch_count,
        )
    except (FileNotFoundError, RuntimeError, ValueError, ZoneInfoNotFoundError) as error:
        raise SystemExit(str(error)) from error

    if len(paths) == 1:
        print(f"群员画像 PNG 已写入：{paths[0].resolve()}")
    else:
        print(f"群员画像 PNG 已写入：共 {len(paths)} 张")


if __name__ == "__main__":
    main()
