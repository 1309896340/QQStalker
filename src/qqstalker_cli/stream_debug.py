"""Standalone streaming-transport debugging for portrait LLM requests."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from src.qqstalker_cli import analyze_transcript, contextual_analysis

DEFAULT_DEBUG_INPUT = Path("exports/20260911194123_消息记录.md")
PREVIEW_CHARACTERS = 200


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="单独调试流式大模型请求；只输出控制台诊断，不生成报告文件"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_DEBUG_INPUT,
        help=f"消息记录 Markdown 文件（默认：{DEFAULT_DEBUG_INPUT}）",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="包含 LLM 配置的文件（默认：.env）",
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
    return parser


def format_preview(content: str) -> str:
    """Show the head and tail of the response without dumping everything."""

    if len(content) <= PREVIEW_CHARACTERS * 2:
        return content
    return f"{content[:PREVIEW_CHARACTERS]}…（中略）…{content[-PREVIEW_CHARACTERS:]}"


def run_debug(args: argparse.Namespace) -> tuple[str, str | None, float, int]:
    """Build one real portrait batch request, run it, and report the summary."""

    analyze_transcript.load_dotenv(args.env_file)
    if not args.input.is_file():
        raise FileNotFoundError(f"消息记录文件不存在：{args.input}")
    transcript = args.input.read_text(encoding="utf-8")
    chronological_messages = contextual_analysis.parse_messages(
        transcript, excluded_members=analyze_transcript.EXCLUDED_MEMBER_NAMES
    )
    members = analyze_transcript.select_members(
        analyze_transcript.extract_member_messages(transcript),
        top_members=args.top_members,
        min_message_count=args.min_message_count,
    )
    if not members:
        raise RuntimeError("没有符合筛选条件的群员")
    max_input_characters = analyze_transcript.positive_integer_setting(
        "LLM_MAX_INPUT_CHARACTERS", analyze_transcript.DEFAULT_MAX_INPUT_CHARACTERS
    )
    member_batch = analyze_transcript.batch_members(
        members,
        max_members=analyze_transcript.positive_integer_setting(
            "LLM_MEMBERS_PER_REQUEST", analyze_transcript.DEFAULT_MEMBERS_PER_REQUEST
        ),
        max_input_characters=max_input_characters,
    )[0]
    message_count = sum(len(messages) for _, messages in member_batch)
    print(
        f"流式调试：使用样本 {args.input}"
        f"（前 {len(member_batch)} 位群员、{message_count} 条消息）",
        flush=True,
    )
    contexts = contextual_analysis.build_member_contexts(
        chronological_messages,
        tuple(member for member, _ in member_batch),
        before=analyze_transcript.nonnegative_integer_setting(
            "LLM_CONTEXT_MESSAGES_BEFORE",
            analyze_transcript.DEFAULT_CONTEXT_MESSAGES_BEFORE,
        ),
        after=analyze_transcript.nonnegative_integer_setting(
            "LLM_CONTEXT_MESSAGES_AFTER",
            analyze_transcript.DEFAULT_CONTEXT_MESSAGES_AFTER,
        ),
        maximum_windows=analyze_transcript.positive_integer_setting(
            "LLM_MAX_CONTEXT_WINDOWS_PER_MEMBER",
            analyze_transcript.DEFAULT_MAX_CONTEXT_WINDOWS_PER_MEMBER,
        ),
        maximum_characters=analyze_transcript.positive_integer_setting(
            "LLM_MAX_CONTEXT_CHARACTERS_PER_MEMBER",
            analyze_transcript.DEFAULT_MAX_CONTEXT_CHARACTERS_PER_MEMBER,
        ),
    )
    prompt = analyze_transcript.build_member_prompt(
        member_batch,
        contexts=contexts,
        max_input_characters=max_input_characters,
    )
    attempt_counter: list[int] = []
    started = time.monotonic()
    content, finish_reason = analyze_transcript.request_portraits(
        prompt,
        base_url=analyze_transcript.required_setting("LLM_BASE_URL"),
        model=analyze_transcript.required_setting("LLM_MODEL"),
        api_key=analyze_transcript.required_setting("LLM_API_KEY"),
        max_tokens=analyze_transcript.positive_integer_setting(
            "LLM_MAX_TOKENS", analyze_transcript.DEFAULT_MAX_TOKENS
        ),
        timeout_seconds=float(
            analyze_transcript.positive_integer_setting(
                "LLM_TIMEOUT_SECONDS", int(analyze_transcript.DEFAULT_TIMEOUT_SECONDS)
            )
        ),
        max_retries=analyze_transcript.nonnegative_integer_setting(
            "LLM_MAX_RETRIES", analyze_transcript.DEFAULT_MAX_RETRIES
        ),
        retry_delay_seconds=analyze_transcript.positive_float_setting(
            "LLM_RETRY_DELAY_SECONDS", analyze_transcript.DEFAULT_RETRY_DELAY_SECONDS
        ),
        stage_label="流式调试",
        attempt_counter=attempt_counter,
    )
    elapsed_seconds = time.monotonic() - started
    attempts = attempt_counter[0] if attempt_counter else 1
    print(
        "流式调试完成："
        f"耗时 {analyze_transcript.format_elapsed_seconds(elapsed_seconds)}，"
        f"接收 {len(content):,} 字，"
        f"结束原因：{finish_reason or '未知'}，"
        f"尝试 {attempts} 次（重试 {max(0, attempts - 1)} 次）",
        flush=True,
    )
    print(f"内容预览：\n{format_preview(content)}", flush=True)
    return content, finish_reason, elapsed_seconds, max(0, attempts - 1)


def main() -> None:
    args = build_argument_parser().parse_args()
    try:
        run_debug(args)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
