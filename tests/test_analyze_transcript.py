"""Tests for transcript member selection and portrait-document assembly."""

from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
import itertools
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import httpx

from src.qqstalker_cli import (
    analyze_transcript,
    concurrency,
    contextual_analysis,
    discussion_analysis,
)

_trace_dir_manager: tempfile.TemporaryDirectory | None = None
_trace_directory_backup: Path | None = None


def setUpModule() -> None:
    """Redirect LLM trace records away from the real temp/ folder for all tests."""

    global _trace_dir_manager, _trace_directory_backup
    _trace_dir_manager = tempfile.TemporaryDirectory()
    _trace_directory_backup = analyze_transcript.TRACE_DIRECTORY
    analyze_transcript.TRACE_DIRECTORY = Path(_trace_dir_manager.name)


def tearDownModule() -> None:
    global _trace_dir_manager, _trace_directory_backup
    assert _trace_dir_manager is not None
    assert _trace_directory_backup is not None
    analyze_transcript.TRACE_DIRECTORY = _trace_directory_backup
    _trace_dir_manager.cleanup()
    _trace_dir_manager = None
    _trace_directory_backup = None


class SelectMembersTests(unittest.TestCase):
    def test_excludes_service_and_system_members_during_extraction(self) -> None:
        """Service and system senders must not contribute to portrait statistics."""

        transcript = """## 2026-09-11 09:00:00 · 测试群

> **普通成员**
>
> 大家好

## 2026-09-11 09:01:00 · 测试群

> **Q群管家**
>
> 入群提示

## 2026-09-11 09:02:00 · 测试群

> **系统消息**
>
> 系统通知
"""

        members = analyze_transcript.extract_member_messages(transcript)

        self.assertEqual([member for member, _ in members], ["普通成员"])

    def test_filters_then_selects_most_active_members(self) -> None:
        """A low-volume member must not occupy a top-member slot."""

        members = [
            ("甲", ["a"] * 2),
            ("乙", ["b"] * 5),
            ("丙", ["c"] * 4),
            ("丁", ["d"] * 1),
        ]

        selected = analyze_transcript.select_members(
            members,
            top_members=2,
            min_message_count=3,
        )

        self.assertEqual([member for member, _ in selected], ["乙", "丙"])

    def test_sorts_by_message_share_when_top_members_is_omitted(self) -> None:
        """The complete portrait list must rank members by message share."""

        members = [
            ("甲", ["a"] * 3),
            ("乙", ["b"] * 5),
            ("丙", ["c"] * 4),
        ]

        selected = analyze_transcript.select_members(
            members,
            top_members=None,
            min_message_count=3,
        )

        self.assertEqual([member for member, _ in selected], ["乙", "丙", "甲"])


class FeaturedQuotesNormalizationTests(unittest.TestCase):
    def test_sample_message_blocks_keeps_budget_and_coverage(self) -> None:
        blocks = [
            f"## 2026-09-12 0{index // 60}:{index % 60:02d}:00 · 群\n\n> **甲**\n>\n> 消息{index}{'内容' * 40}\n\n"
            for index in range(100)
        ]
        transcript = "# 群聊记录\n\n" + "".join(blocks)

        self.assertEqual(
            analyze_transcript.sample_message_blocks(transcript, 10**9), transcript
        )
        sampled = analyze_transcript.sample_message_blocks(transcript, 2_000)
        self.assertLessEqual(len(sampled), 2_000)
        self.assertIn("消息0", sampled)
        self.assertTrue(any(f"消息{index}" in sampled for index in range(90, 100)))
        self.assertLess(sampled.count("## 2026-09-12"), 100)

    def test_analyze_featured_quotes_bounds_transcript_input(self) -> None:
        blocks = [
            f"## 2026-09-12 0{index // 60}:{index % 60:02d}:00 · 群\n\n> **甲**\n>\n> 消息{index}{'内容' * 40}\n\n"
            for index in range(100)
        ]
        transcript = "# 群聊记录\n\n" + "".join(blocks)
        captured: list[str] = []

        def fake_request(prompt: str, **_kwargs: object) -> tuple[str, str | None]:
            captured.append(prompt)
            return "成员：甲\n语录：消息0\n点评：测试。", None

        with patch.object(
            analyze_transcript, "request_portraits", side_effect=fake_request
        ):
            analyze_transcript.analyze_featured_quotes(
                transcript,
                quote_count=5,
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
                max_input_characters=2_000,
            )

        self.assertEqual(len(captured), 1)
        self.assertLessEqual(len(captured[0]), 2_000)
        self.assertIn("消息0", captured[0])
        self.assertTrue(any(f"消息{index}" in captured[0] for index in range(90, 100)))

    def test_parses_plain_text_fields_per_member(self) -> None:
        """The requested plain 成员/语录/点评 lines must parse into records."""

        records = analyze_transcript.parse_featured_quotes(
            "成员：甲\n语录：这也太逆天了\n点评：荒诞反差强烈。\n\n"
            "成员：乙\n语录：第二句\n点评：点评二。"
        )

        self.assertEqual(
            records,
            [
                ("甲", [("这也太逆天了", "荒诞反差强烈。")]),
                ("乙", [("第二句", "点评二。")]),
            ],
        )

    def test_groups_flat_list_items_by_member(self) -> None:
        """Legacy flat bullet output must regroup all quotes per member."""

        records = analyze_transcript.parse_featured_quotes(
            "- **甲**\n  > 第一条语录\n\n- **点评**：点评一。\n\n"
            "- **乙**\n  > 第二条语录\n\n- **点评**：点评二。\n\n"
            "- **甲**\n  > 第三条语录\n\n- **点评**：点评三。"
        )

        self.assertEqual(
            records,
            [
                (
                    "甲",
                    [("第一条语录", "点评一。"), ("第三条语录", "点评三。")],
                ),
                ("乙", [("第二条语录", "点评二。")]),
            ],
        )

    def test_groups_bold_names_inside_blockquotes(self) -> None:
        """Blockquote-first output must still split quotes per member."""

        records = analyze_transcript.parse_featured_quotes(
            "### 精选语录\n\n> **甲**\n> 第一条语录\n\n- **点评**：点评一。\n\n"
            "> **乙**\n> 第二条语录\n\n- **点评**：点评二。"
        )

        self.assertEqual(
            records,
            [
                ("甲", [("第一条语录", "点评一。")]),
                ("乙", [("第二条语录", "点评二。")]),
            ],
        )

    def test_drops_member_without_parsable_quote(self) -> None:
        """A member heading with no quote text must not produce an empty card."""

        records = analyze_transcript.parse_featured_quotes(
            "### 甲\n\n### 乙\n> 唯一语录\n\n- **点评**：点评。"
        )

        self.assertEqual(records, [("乙", [("唯一语录", "点评。")])])

    def test_returns_no_records_when_nothing_parses(self) -> None:
        """Unrecognized output must parse to nothing so the raw text is kept."""

        self.assertEqual(
            analyze_transcript.parse_featured_quotes("模型自由发挥的普通段落。"), []
        )

    def test_document_renders_quote_structure_itself(self) -> None:
        """The document must hardcode per-member headings from parsed records."""

        analysis = analyze_transcript.build_analysis_document(
            member_count=2,
            portraits=("### 甲\n- 简洁概括",),
            featured_quotes=(
                "- **甲**\n  > 语录一\n\n- **点评**：点评一。\n\n"
                "- **乙**\n  > 语录二\n\n- **点评**：点评二。"
            ),
        )

        quotes_section = analysis[analysis.index("## 语录精选") :]
        self.assertEqual(
            quotes_section,
            "## 语录精选\n\n### 甲\n\n> 语录一\n\n- **点评**：点评一。\n\n"
            "### 乙\n\n> 语录二\n\n- **点评**：点评二。",
        )

    def test_document_keeps_raw_quotes_when_parsing_fails(self) -> None:
        """Unparsable quote output must fall back to the raw model text."""

        raw = "模型自由发挥的普通段落。"
        analysis = analyze_transcript.build_analysis_document(
            member_count=1,
            portraits=("### 甲\n- 简洁概括",),
            featured_quotes=raw,
        )

        self.assertIn(f"## 语录精选\n\n{raw}", analysis)


class AnalysisDocumentTests(unittest.TestCase):
    def test_joins_member_portraits_without_batch_headings(self) -> None:
        """Rendered analysis must contain portraits directly, regardless of request batches."""

        analysis = analyze_transcript.build_analysis_document(
            member_count=2,
            portraits=("### 甲\n- 简洁概括", "### 乙\n- 简洁概括"),
        )

        self.assertIn("### 甲", analysis)
        self.assertIn("### 乙", analysis)
        self.assertNotIn("群员批次", analysis)

    def test_appends_featured_quotes_after_member_portraits(self) -> None:
        """The quote feature must form a dedicated final topic in the document."""

        analysis = analyze_transcript.build_analysis_document(
            member_count=1,
            portraits=("### 甲\n- 简洁概括",),
            featured_quotes="### 甲\n> 这也太逆天了\n\n- **点评**：荒诞反差强烈。",
        )

        self.assertIn("## 语录精选", analysis)
        self.assertLess(analysis.index("### 甲"), analysis.index("## 语录精选"))

    def test_includes_fixed_group_overview_fields(self) -> None:
        """The first report section combines exact facts and the model overview."""

        analysis = analyze_transcript.build_analysis_document(
            member_count=2,
            portraits=("### 甲\n- 简洁概括",),
            overview="- **群体氛围**：讨论直接。",
            top_member=("甲", ["消息"] * 6),
            total_message_count=10,
            primary_activity_period="晚间为主",
        )

        self.assertIn("## 群像速览", analysis)
        self.assertIn("**分析成员**：2 位", analysis)
        self.assertIn("**主要活跃时段**：晚间为主", analysis)
        self.assertIn("**头部活跃**：甲（6 条，占 60.0%）", analysis)
        self.assertIn("**群体氛围**：讨论直接。", analysis)


class PromptTests(unittest.TestCase):
    def test_requires_an_unlabeled_quoted_viewpoint_for_each_member(self) -> None:
        """Portrait prompts must use a standalone quote instead of a labeled field."""

        prompt = analyze_transcript.build_member_prompt((("甲", ["一条消息"]),))

        self.assertNotIn("**精选语录**", prompt)
        self.assertIn("> **“最具代表性的具体观点或语录", prompt)
        self.assertIn("不超过 25 字", prompt)
        self.assertIn("忠实的简短转述", prompt)
        self.assertNotIn("互动与表达", prompt)

    def test_requests_expanded_portraits_and_two_role_tags(self) -> None:
        """Portrait prompts must leave enough room for a useful member summary."""

        prompt = analyze_transcript.build_member_prompt((("甲", ["一条消息"]),))

        self.assertIn("两个互补的短标签", prompt)
        self.assertIn("80–120 字", prompt)
        self.assertIn("表达特点、持续关注点及在群内的互动方式", prompt)

    def test_requests_high_quality_group_quotes(self) -> None:
        """The quote prompt must follow the requested humorous and provocative criteria."""

        prompt = analyze_transcript.build_featured_quotes_prompt(
            "## 2026-09-11 09:00:00 · 测试群\n\n> **甲**\n>\n> 一条发言",
            quote_count=8,
        )

        self.assertIn("幽默、讽刺或“逆天”程度", prompt)
        self.assertIn("精选 8 条", prompt)
        self.assertIn("成员：成员名称", prompt)
        self.assertIn("语录：语录原文", prompt)
        self.assertIn("点评：点评内容", prompt)
        self.assertIn("不要使用任何 Markdown 标记", prompt)
        self.assertIn("QQ 表情", prompt)
        self.assertIn("从展示语录中去除", prompt)
        self.assertIn("去除后没有文字内容的发言不得入选", prompt)

    def test_requests_cautious_fixed_group_overview(self) -> None:
        """The overview prompt must remain grounded in completed portraits."""

        prompt = analyze_transcript.build_group_overview_prompt(("### 甲\n- **关注话题**：游戏",))

        self.assertIn("仅根据提供的画像概括", prompt)
        self.assertIn("**群体氛围**", prompt)
        self.assertIn("**主导话题**", prompt)
        self.assertIn("**整体画像**", prompt)


class BatchProgressTests(unittest.TestCase):
    def test_formats_all_member_names_in_batch_order(self) -> None:
        """Batch progress must identify every member included in the request."""

        names = analyze_transcript.format_member_batch_names(
            (("甲", ["消息"]), ("乙", ["消息"]), ("丙", ["消息"]))
        )

        self.assertEqual(names, "甲、乙、丙")


class ProgressReportingTests(unittest.TestCase):
    def test_formats_durations_in_compact_chinese_units(self) -> None:
        """Elapsed-time reporting must stay readable across the minute boundary."""

        self.assertEqual(analyze_transcript.format_elapsed_seconds(0), "0 秒")
        self.assertEqual(analyze_transcript.format_elapsed_seconds(59.4), "59 秒")
        self.assertEqual(analyze_transcript.format_elapsed_seconds(60), "1 分钟")
        self.assertEqual(analyze_transcript.format_elapsed_seconds(135), "2 分 15 秒")

    def test_heartbeat_reports_elapsed_wait_while_a_request_blocks(self) -> None:
        """Long LLM waits must emit periodic heartbeat lines with the wait duration."""

        output = StringIO()
        with redirect_stdout(output):
            with analyze_transcript.llm_wait_heartbeat(
                "画像批次 1/1", interval_seconds=0.01
            ):
                time.sleep(0.05)

        self.assertIn("画像批次 1/1：大模型仍在生成", output.getvalue())
        self.assertIn("已等待 ", output.getvalue())

    def test_heartbeat_stays_silent_for_short_waits(self) -> None:
        """Fast responses must not produce heartbeat noise."""

        output = StringIO()
        with redirect_stdout(output):
            with analyze_transcript.llm_wait_heartbeat(
                "画像批次 1/1", interval_seconds=1.0
            ):
                time.sleep(0.01)

        self.assertNotIn("大模型仍在生成", output.getvalue())

    def test_request_portraits_reports_stage_request_and_completion_lines(self) -> None:
        """Each labeled request must announce its input size and report elapsed time."""

        request = httpx.Request("POST", "https://example.test/chat/completions")
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": "### 甲"}}]}
        output = StringIO()
        with (
            tempfile.TemporaryDirectory() as trace_dir,
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.object(analyze_transcript, "TRACE_DIRECTORY", Path(trace_dir)),
            patch.dict(os.environ, {"LLM_STREAM": "0"}),
            redirect_stdout(output),
        ):
            client = client_class.return_value.__enter__.return_value
            client.post.return_value = response
            content, _ = analyze_transcript.request_portraits(
                "测试请求",
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
                stage_label="画像批次 1/1",
                progress_interval_seconds=0,
            )

        progress_text = output.getvalue()
        self.assertIn("### 甲", content)
        self.assertIn("画像批次 1/1：正在请求大模型（非流式，输入 4 字", progress_text)
        self.assertIn("最长等待 1 秒", progress_text)
        self.assertIn("画像批次 1/1：大模型响应完成", progress_text)
        self.assertIn("输出 5 字", progress_text)

    def test_analyze_all_members_reports_stage_lines_and_batch_labels(self) -> None:
        """The pipeline must expose its stages and pass labels to every request."""

        transcript = """## 2026-09-11 19:00:00 · 测试群

> **甲**
>
> 消息一

## 2026-09-11 20:00:00 · 测试群

> **乙**
>
> 消息二
"""
        with patch.object(
            analyze_transcript,
            "request_portraits",
            side_effect=(
                (
                    "### 甲\n- **角色定位**：活跃成员\n\n### 乙\n- **角色定位**：安静成员",
                    None,
                ),
                ("- **群体氛围**：讨论直接。", None),
                (
                    '{"topics":[{"id":"t1","title":"测试议题",'
                    '"summary":"摘要","start_line":1,"end_line":2}]}',
                    None,
                ),
                ("甲表达了一个观点并作出总结。乙补充了不同信息并支持继续讨论。", None),
                ("", None),
            ),
        ) as request_mock:
            output = StringIO()
            with redirect_stdout(output):
                analyze_transcript.analyze_all_members(
                    transcript,
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    members_per_request=2,
                    max_input_characters=1_000,
                    top_members=None,
                    min_message_count=0,
                )

        progress_text = output.getvalue()
        self.assertIn("符合条件的群员 2 位", progress_text)
        self.assertIn("将分为 1 个批次请求大模型", progress_text)
        self.assertIn("已构建 2 位成员的对话上下文", progress_text)
        self.assertIn("正在生成讨论纪要", progress_text)
        stage_labels = [call.kwargs["stage_label"] for call in request_mock.call_args_list]
        self.assertEqual(stage_labels[0], "画像批次 1/1")
        self.assertEqual(stage_labels[1], "群像速览")
        self.assertEqual(stage_labels[2], "讨论纪要（第 1 次请求）")
        self.assertEqual(stage_labels[3], "讨论纪要（第 2 次请求）")
        self.assertEqual(stage_labels[4], "语录精选")


class LlmTraceTests(unittest.TestCase):
    """Every LLM call must leave a paired request/response record under temp/."""

    def test_request_and_response_traces_are_written_for_each_call(self) -> None:
        """A non-stream call writes a truncated request preview and full response."""

        long_prompt = "长" * 250
        full_response = "模型回答" * 50
        with tempfile.TemporaryDirectory() as trace_dir:
            directory = Path(trace_dir)
            response = MagicMock()
            response.json.return_value = {
                "choices": [{"message": {"content": full_response}}]
            }
            with (
                patch.object(analyze_transcript.httpx, "Client") as client_class,
                patch.object(analyze_transcript, "TRACE_DIRECTORY", directory),
                patch.dict(os.environ, {"LLM_STREAM": "0"}),
            ):
                client = client_class.return_value.__enter__.return_value
                client.post.return_value = response
                analyze_transcript.request_portraits(
                    long_prompt,
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    stage_label="画像批次 1/1",
                    progress_interval_seconds=0,
                )

            request_files = list(directory.glob("*_request.md"))
            response_files = list(directory.glob("*_response.md"))
            self.assertEqual(len(request_files), 1)
            self.assertEqual(len(response_files), 1)
            self.assertEqual(
                request_files[0].name.removesuffix("_request.md"),
                response_files[0].name.removesuffix("_response.md"),
            )
            request_text = request_files[0].read_text(encoding="utf-8")
            self.assertIn("画像批次 1/1", request_text)
            self.assertIn("长" * 100 + "...", request_text)
            self.assertNotIn("长" * 101, request_text)
            response_text = response_files[0].read_text(encoding="utf-8")
            self.assertIn(full_response, response_text)
            self.assertNotIn("reasoning", response_text)

    def test_trace_names_stay_unique_within_the_same_second(self) -> None:
        """Concurrent same-second calls must not overwrite each other's records."""

        with tempfile.TemporaryDirectory() as trace_dir:
            directory = Path(trace_dir)
            with patch.object(analyze_transcript, "TRACE_DIRECTORY", directory):
                first = analyze_transcript.write_llm_request_trace(
                    stage_label="A", model="m", prompt="p"
                )
                second = analyze_transcript.write_llm_request_trace(
                    stage_label="B", model="m", prompt="q"
                )
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            assert first is not None and second is not None
            self.assertNotEqual(first, second)
            written = {item.name for item in directory.glob("*.md")}
            expected = {
                first.name.replace("_response.md", "_request.md"),
                second.name.replace("_response.md", "_request.md"),
            }
            self.assertEqual(written, expected)


def make_stream_context(
    lines: tuple[str, ...] = (),
    error: Exception | None = None,
) -> MagicMock:
    """Build a mock httpx streaming context manager, optionally failing mid-stream."""

    response = MagicMock()
    if error is not None:
        def iter_lines():
            yield from lines
            raise error

        response.iter_lines.side_effect = iter_lines
    else:
        response.iter_lines.return_value = iter(lines)
    context = MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False
    return context


class SseStreamAccumulatorTests(unittest.TestCase):
    """SSE line parsing must tolerate provider-specific stream variants."""

    STREAM_CASES = (
        (
            "standard_content_stream",
            (
                'data: {"choices":[{"delta":{"content":"### 甲"}}]}',
                "",
                ": keep-alive",
                'data: {"choices":[{"delta":{"content":"\\n- 概括"}}]}',
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
                "data: [DONE]",
            ),
            "### 甲\n- 概括",
            "stop",
            True,
        ),
        (
            "usage_end_block_without_choices",
            (
                'data: {"choices":[{"delta":{"content":"结论"}}]}',
                'data: {"usage":{"total_tokens":10}}',
            ),
            "结论",
            None,
            False,
        ),
        (
            "stream_without_done_marker",
            (
                'data: {"choices":[{"delta":{"content":"A"}}]}',
                'data: {"choices":[{"delta":{"content":"B"},"finish_reason":"stop"}]}',
            ),
            "AB",
            "stop",
            False,
        ),
        (
            "legacy_text_field_deltas",
            (
                'data: {"choices":[{"text":"画像"}]}',
                'data: {"choices":[{"text":"正文"}]}',
            ),
            "画像正文",
            None,
            False,
        ),
        (
            "unparseable_payload_is_skipped",
            (
                "data: not-json",
                'data: {"choices":[{"delta":{"content":"有效"}}]}',
            ),
            "有效",
            None,
            False,
        ),
    )

    def test_parses_known_stream_variants(self) -> None:
        for name, lines, expected_text, expected_finish, expected_done in self.STREAM_CASES:
            with self.subTest(case=name):
                accumulator = analyze_transcript.SseStreamAccumulator()
                done = False
                for line in lines:
                    if accumulator.feed_line(line):
                        done = True
                        break
                self.assertEqual(accumulator.text, expected_text)
                self.assertEqual(accumulator.finish_reason, expected_finish)
                self.assertEqual(done, expected_done)
                self.assertEqual(accumulator.received_characters, len(accumulator.text))

    def test_reasoning_only_stream_falls_back_to_reasoning_text(self) -> None:
        accumulator = analyze_transcript.SseStreamAccumulator()
        for line in (
            'data: {"choices":[{"delta":{"reasoning_content":"思考"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        ):
            accumulator.feed_line(line)

        self.assertEqual(accumulator.text, "思考")

    def test_reasoning_is_ignored_once_content_exists(self) -> None:
        accumulator = analyze_transcript.SseStreamAccumulator()
        for line in (
            'data: {"choices":[{"delta":{"reasoning_content":"思考"}}]}',
            'data: {"choices":[{"delta":{"content":"正文"}}]}',
        ):
            accumulator.feed_line(line)

        self.assertEqual(accumulator.text, "正文")


class BooleanSettingTests(unittest.TestCase):
    def test_accepts_true_false_default_and_rejects_invalid_values(self) -> None:
        for value, expected in (
            ("1", True),
            ("true", True),
            ("YES", True),
            ("on", True),
            ("0", False),
            ("False", False),
            ("no", False),
            ("off", False),
        ):
            with patch.dict(os.environ, {"LLM_STREAM": value}):
                self.assertEqual(
                    analyze_transcript.boolean_setting("LLM_STREAM", True), expected
                )
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(analyze_transcript.boolean_setting("LLM_STREAM", True))
            self.assertFalse(analyze_transcript.boolean_setting("LLM_STREAM", False))
        with patch.dict(os.environ, {"LLM_STREAM": "maybe"}):
            with self.assertRaisesRegex(RuntimeError, "LLM_STREAM"):
                analyze_transcript.boolean_setting("LLM_STREAM", True)


class FakeTerminal(StringIO):
    """A stdout double that claims to be an interactive terminal."""

    def isatty(self) -> bool:
        return True


class RecordingReporter:
    """A reporter double recording the progress protocol calls in order."""

    def __init__(self) -> None:
        self.events: list[tuple[object, ...]] = []
        self.messages: list[str] = []

    def begin(self, label: str) -> object:
        token = f"token-{len(self.events)}"
        self.events.append(("begin", token, label))
        return token

    def update(
        self,
        token: object,
        *,
        received_chars: int = 0,
        note: str | None = None,
    ) -> None:
        self.events.append(("update", token, received_chars, note))

    def finish(self, token: object, *, ok: bool = True) -> None:
        self.events.append(("finish", token, ok))

    def print(self, text: str) -> None:
        self.messages.append(text)

    def close(self) -> None:
        return None

    def __enter__(self) -> "RecordingReporter":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None


class StreamingTransportTests(unittest.TestCase):
    """The streaming path must accumulate, report progress, and retry drops."""

    STREAM_LINES = (
        'data: {"choices":[{"delta":{"content":"### 甲"}}]}',
        ": keep-alive",
        'data: {"choices":[{"delta":{"content":"\\n- 概括"}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        "data: [DONE]",
    )

    def test_streaming_round_trip_returns_text_finish_reason_and_progress(self) -> None:
        attempts: list[int] = []
        output = StringIO()
        with (
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.object(
                analyze_transcript.time, "monotonic", side_effect=itertools.count(0, 8)
            ),
            patch.dict(os.environ, {"LLM_STREAM": "1"}),
            redirect_stdout(output),
        ):
            client = client_class.return_value.__enter__.return_value
            client.stream.return_value = make_stream_context(self.STREAM_LINES)
            content, finish_reason = analyze_transcript.request_portraits(
                "测试请求",
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
                stage_label="画像批次 1/1",
                progress_interval_seconds=0.001,
                attempt_counter=attempts,
            )

        progress_text = output.getvalue()
        self.assertEqual(content, "### 甲\n- 概括")
        self.assertEqual(finish_reason, "stop")
        self.assertEqual(attempts, [1])
        self.assertEqual(client.stream.call_count, 1)
        self.assertEqual(client.post.call_count, 0)
        self.assertIn("正在请求大模型（流式，输入 4 字", progress_text)
        self.assertIn("大模型响应完成", progress_text)
        self.assertIn("输出 10 字", progress_text)
        self.assertNotIn("大模型正在生成", progress_text)
        self.assertNotIn("\r", progress_text)
        self.assertNotIn("\x1b[", progress_text)

    def test_mid_stream_disconnect_retries_and_keeps_full_result(self) -> None:
        request = httpx.Request("POST", "https://example.test/chat/completions")
        output = StringIO()
        with (
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.dict(os.environ, {"LLM_STREAM": "1"}),
            redirect_stdout(output),
        ):
            client = client_class.return_value.__enter__.return_value
            client.stream.side_effect = [
                make_stream_context(
                    lines=('data: {"choices":[{"delta":{"content":"部分"}}]}',),
                    error=httpx.RemoteProtocolError(
                        "Server disconnected without sending a response.",
                        request=request,
                    ),
                ),
                make_stream_context(self.STREAM_LINES),
            ]
            content, finish_reason = analyze_transcript.request_portraits(
                "测试请求",
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
                max_retries=1,
                retry_delay_seconds=0.01,
                stage_label="画像批次 1/1",
                progress_interval_seconds=0,
            )

        self.assertEqual(content, "### 甲\n- 概括")
        self.assertEqual(finish_reason, "stop")
        self.assertEqual(client.stream.call_count, 2)
        self.assertIn("流式已接收 2 字后中断", output.getvalue())

    def test_streaming_length_finish_reason_returns_without_retry(self) -> None:
        with (
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.dict(os.environ, {"LLM_STREAM": "1"}),
        ):
            client = client_class.return_value.__enter__.return_value
            client.stream.return_value = make_stream_context(
                (
                    'data: {"choices":[{"delta":{"content":"被截断"}}]}',
                    'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
                    "data: [DONE]",
                )
            )
            content, finish_reason = analyze_transcript.request_portraits(
                "测试请求",
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
                max_retries=3,
            )

        self.assertEqual((content, finish_reason), ("被截断", "length"))
        self.assertEqual(client.stream.call_count, 1)

    def test_stream_switch_selects_the_transport(self) -> None:
        with (
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.dict(os.environ, {"LLM_STREAM": "0"}),
        ):
            client = client_class.return_value.__enter__.return_value
            post_response = MagicMock()
            post_response.json.return_value = {
                "choices": [{"message": {"content": "文本"}}]
            }
            client.post.return_value = post_response
            content, _ = analyze_transcript.request_portraits(
                "测试请求",
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
            )
            self.assertEqual(content, "文本")
            self.assertEqual(client.post.call_count, 1)
            self.assertEqual(client.stream.call_count, 0)
        with (
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.dict(os.environ, {}, clear=True),
        ):
            client = client_class.return_value.__enter__.return_value
            client.stream.return_value = make_stream_context(self.STREAM_LINES)
            content, _ = analyze_transcript.request_portraits(
                "测试请求",
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
            )
            self.assertEqual(content, "### 甲\n- 概括")
            self.assertEqual(client.stream.call_count, 1)
            self.assertEqual(client.post.call_count, 0)

    def test_streaming_reports_progress_through_the_reporter(self) -> None:
        """The reporter must receive the begin/update/finish sequence in order."""

        reporter = RecordingReporter()
        with (
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.object(
                analyze_transcript.time, "monotonic", side_effect=itertools.count(0, 8)
            ),
            patch.dict(os.environ, {"LLM_STREAM": "1"}),
        ):
            client = client_class.return_value.__enter__.return_value
            client.stream.return_value = make_stream_context(self.STREAM_LINES)
            analyze_transcript.request_portraits(
                "测试请求",
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
                stage_label="画像批次 1/1",
                progress_interval_seconds=15,
                reporter=reporter,
            )

        tokens = [event[1] for event in reporter.events if event[0] == "begin"]
        self.assertEqual(len(tokens), 1)
        token = tokens[0]
        self.assertEqual(reporter.events[0], ("begin", token, "画像批次 1/1"))
        updates = [event for event in reporter.events if event[0] == "update"]
        self.assertEqual(len(updates), 4)
        self.assertEqual(updates[-1][2], 10)
        self.assertEqual(reporter.events[-1], ("finish", token, True))
        self.assertIn("画像批次 1/1：大模型响应完成", "\n".join(reporter.messages))

    def test_reporter_failure_marks_the_row_as_failed(self) -> None:
        """An unrecoverable request must finish with ok=False on the reporter."""

        reporter = RecordingReporter()
        with (
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.dict(os.environ, {"LLM_STREAM": "1"}),
        ):
            client = client_class.return_value.__enter__.return_value
            client.stream.side_effect = httpx.ConnectError("连接失败")
            with self.assertRaisesRegex(RuntimeError, "无法连接大模型服务"):
                analyze_transcript.request_portraits(
                    "测试请求",
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    max_retries=0,
                    stage_label="画像批次 1/1",
                    reporter=reporter,
                )

        self.assertEqual(reporter.events[-1], ("finish", reporter.events[0][1], False))

    def test_heartbeat_reports_through_the_on_wait_callback(self) -> None:
        """The non-stream heartbeat must deliver elapsed seconds to the callback."""

        waits: list[float] = []
        with analyze_transcript.llm_wait_heartbeat(
            "画像批次 1/1", interval_seconds=0.01, on_wait=waits.append
        ):
            time.sleep(0.05)

        self.assertGreaterEqual(len(waits), 1)

    def test_heartbeat_prints_lines_without_a_callback(self) -> None:
        """Without a reporter the heartbeat must keep printing elapsed lines."""

        output = StringIO()
        with redirect_stdout(output):
            with analyze_transcript.llm_wait_heartbeat(
                "画像批次 1/1", interval_seconds=0.01
            ):
                time.sleep(0.05)

        self.assertIn("大模型仍在生成", output.getvalue())


class MemberBatchRecoveryTests(unittest.TestCase):
    def test_skips_a_member_when_the_individual_recovery_is_truncated(self) -> None:
        """One bad recovery must not abort portraits that the batch already produced."""

        skipped_members: list[str] = []
        with patch.object(
            analyze_transcript,
            "request_portraits",
            side_effect=(
                ("### 甲\n- **角色定位**：活跃成员", None),
                ("", "length"),
            ),
        ):
            output = StringIO()
            with redirect_stdout(output):
                analysis = analyze_transcript.analyze_member_batch(
                    (("甲", ["消息"]), ("乙", ["消息"])),
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    skipped_members=skipped_members,
                )

        self.assertIn("### 甲", analysis)
        self.assertNotIn("### 乙", analysis)
        self.assertEqual(skipped_members, ["乙"])
        self.assertIn("已跳过并继续处理其他成员", output.getvalue())

    def test_report_count_excludes_skipped_members(self) -> None:
        """Overview facts must describe only portraits that were successfully produced."""

        transcript = """## 2026-09-11 19:00:00 · 测试群

> **甲**
>
> 消息一

## 2026-09-11 20:00:00 · 测试群

> **乙**
>
> 消息二
"""
        with patch.object(
            analyze_transcript,
            "request_portraits",
            side_effect=(
                ("### 甲\n- **角色定位**：活跃成员", None),
                ("", "length"),
                ("- **群体氛围**：讨论直接。", None),
                (
                    '{"topics":[{"id":"t1","title":"测试议题",'
                    '"summary":"摘要","start_line":1,"end_line":2}]}',
                    None,
                ),
                ("甲表达了一个观点并作出总结。乙补充了不同信息并支持继续讨论。", None),
                ("", None),
            ),
        ):
            analysis = analyze_transcript.analyze_all_members(
                transcript,
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
                members_per_request=2,
                max_input_characters=1_000,
                top_members=None,
                min_message_count=0,
            )

        self.assertIn("**分析成员**：1 位", analysis.markdown)
        self.assertNotIn("**分析成员**：2 位", analysis.markdown)


class PortraitBatchConcurrencyTests(unittest.TestCase):
    """Parallel portrait batches must stay ordered, bounded, and complete."""

    @staticmethod
    def _transcript(members: tuple[str, ...]) -> str:
        blocks = []
        for member_index, member in enumerate(members):
            for offset in range(2):
                blocks.append(
                    f"## 2026-09-11 19:0{member_index}:{offset:02d} · 测试群\n\n"
                    f"> **{member}**\n>\n> 消息{offset}"
                )
        return "\n\n".join(blocks)

    @staticmethod
    def _member_from_prompt(prompt: str) -> str | None:
        match = re.search(r"(?m)^- (\S+)（本批次", prompt)
        return match.group(1) if match else None

    def test_portrait_concurrency_defaults_and_rejects_invalid_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                analyze_transcript.positive_integer_setting(
                    "LLM_PORTRAIT_CONCURRENCY",
                    analyze_transcript.DEFAULT_PORTRAIT_CONCURRENCY,
                ),
                2,
            )
        for value in ("0", "-1", "many"):
            with patch.dict(
                os.environ, {"LLM_PORTRAIT_CONCURRENCY": value}, clear=False
            ), self.assertRaisesRegex(RuntimeError, "LLM_PORTRAIT_CONCURRENCY"):
                analyze_transcript.positive_integer_setting(
                    "LLM_PORTRAIT_CONCURRENCY", 2
                )

    def test_parallel_batches_preserve_order_and_bound_concurrency(self) -> None:
        """Four batches at concurrency 2 must never exceed two in-flight requests."""

        transcript = self._transcript(("甲", "乙", "丙", "丁"))
        lock = threading.Lock()
        active = 0
        peak = 0

        def fake_request(prompt: str, **_kwargs: object) -> tuple[str, str | None]:
            nonlocal active, peak
            member = self._member_from_prompt(prompt)
            if member is not None:
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return f"### {member}\n- **角色定位**：测试标签", None
            if "群像速览" in prompt:
                return "- **整体画像**：测试概括。", None
            return "", None

        with patch.object(
            analyze_transcript, "request_portraits", side_effect=fake_request
        ), patch.object(
            analyze_transcript.discussion_analysis,
            "analyze_discussion_minutes",
            return_value=discussion_analysis.DiscussionReport((), (), (), "day"),
        ), patch.object(
            analyze_transcript, "analyze_featured_quotes", return_value="暂无。"
        ):
            output = StringIO()
            with redirect_stdout(output):
                analysis = analyze_transcript.analyze_all_members(
                    transcript,
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    members_per_request=1,
                    max_input_characters=1_000,
                    top_members=None,
                    min_message_count=0,
                    portrait_concurrency=2,
                )

        self.assertEqual(peak, 2)
        markdown = analysis.markdown
        positions = [markdown.index(f"### {member}") for member in ("甲", "乙", "丙", "丁")]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("**分析成员**：4 位", markdown)
        self.assertIn("画像批次将并发请求大模型（独立批次最多 2 个并发）", output.getvalue())

    def test_single_batch_at_default_concurrency_stays_sequential(self) -> None:
        """One batch must not trigger the parallel notice or a thread pool."""

        transcript = self._transcript(("甲", "乙"))

        def fake_request(prompt: str, **_kwargs: object) -> tuple[str, str | None]:
            member = self._member_from_prompt(prompt)
            if member is not None:
                return f"### {member}\n- **角色定位**：测试标签", None
            if "群像速览" in prompt:
                return "- **整体画像**：测试概括。", None
            return "", None

        with patch.object(
            analyze_transcript, "request_portraits", side_effect=fake_request
        ), patch.object(
            analyze_transcript.discussion_analysis,
            "analyze_discussion_minutes",
            return_value=discussion_analysis.DiscussionReport((), (), (), "day"),
        ), patch.object(
            analyze_transcript, "analyze_featured_quotes", return_value="暂无。"
        ):
            output = StringIO()
            with redirect_stdout(output):
                analyze_transcript.analyze_all_members(
                    transcript,
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    members_per_request=2,
                    max_input_characters=1_000,
                    top_members=None,
                    min_message_count=0,
                )

        self.assertNotIn("画像批次将并发请求大模型", output.getvalue())

    def test_skipped_members_survive_parallel_batches(self) -> None:
        """Members skipped in different parallel batches must all be recorded."""

        transcript = self._transcript(("甲", "乙", "丙", "丁"))
        failure_names = {"乙", "丁"}
        request_counts: dict[str, int] = {}
        lock = threading.Lock()
        active = 0
        peak = 0

        def fake_request(prompt: str, **_kwargs: object) -> tuple[str, str | None]:
            nonlocal active, peak
            member = self._member_from_prompt(prompt)
            if member is not None:
                with lock:
                    active += 1
                    peak = max(peak, active)
                    request_counts[member] = request_counts.get(member, 0) + 1
                time.sleep(0.02)
                with lock:
                    active -= 1
                attempt = request_counts[member]
                if member in failure_names:
                    # 批次请求缺少成员标题触发补偿，补偿请求再次截断则跳过。
                    if attempt == 1:
                        return "与本批次成员无关的说明文字", None
                    return "", "length"
                return f"### {member}\n- **角色定位**：测试标签", None
            if "群像速览" in prompt:
                return "- **整体画像**：测试概括。", None
            return "", None

        with patch.object(
            analyze_transcript, "request_portraits", side_effect=fake_request
        ), patch.object(
            analyze_transcript.discussion_analysis,
            "analyze_discussion_minutes",
            return_value=discussion_analysis.DiscussionReport((), (), (), "day"),
        ), patch.object(
            analyze_transcript, "analyze_featured_quotes", return_value="暂无。"
        ):
            analysis = analyze_transcript.analyze_all_members(
                transcript,
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
                members_per_request=1,
                max_input_characters=1_000,
                top_members=None,
                min_message_count=0,
                portrait_concurrency=2,
            )

        self.assertEqual(peak, 2)
        markdown = analysis.markdown
        self.assertIn("### 甲", markdown)
        self.assertIn("### 丙", markdown)
        self.assertNotIn("### 乙", markdown)
        self.assertNotIn("### 丁", markdown)
        self.assertIn("**分析成员**：2 位", markdown)
        self.assertEqual(request_counts["乙"], 2)
        self.assertEqual(request_counts["丁"], 2)


class ConcurrentProgressIntegrationTests(unittest.TestCase):
    def test_concurrent_requests_hold_rows_simultaneously(self) -> None:
        """Concurrent discussion requests must share one multi-row progress area."""

        reporter = RecordingReporter()
        barrier = threading.Barrier(4)
        peak_open_rows = 0
        open_rows = 0
        lock = threading.Lock()

        def worker(item: int) -> int:
            nonlocal peak_open_rows, open_rows
            token = reporter.begin(f"讨论纪要（第 {item} 次请求）")
            with lock:
                open_rows += 1
                peak_open_rows = max(peak_open_rows, open_rows)
            barrier.wait(timeout=5)
            reporter.update(token, received_chars=10)
            reporter.finish(token, ok=True)
            with lock:
                open_rows -= 1
            return item

        results = concurrency.run_items(
            (1, 2, 3, 4), worker=worker, maximum_workers=4
        )

        self.assertEqual(results, (1, 2, 3, 4))
        self.assertEqual(peak_open_rows, 4)
        begins = [event for event in reporter.events if event[0] == "begin"]
        finishes = [event for event in reporter.events if event[0] == "finish"]
        self.assertEqual(len(begins), 4)
        self.assertTrue(all(event[2] is True for event in finishes))


class FakeTtyStream:
    """Minimal tty-like stream so truncation prompts enter interactive mode."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        return None


class TruncationChoiceTests(unittest.TestCase):
    """Output truncation must ask the user instead of aborting outright."""

    def test_truncated_output_retries_without_token_cap_when_user_confirms(
        self,
    ) -> None:
        """Confirming the prompt re-sends the request without the max_tokens cap."""

        captured_limits: list[int | None] = []

        def fake_request(prompt: str, **kwargs: object) -> tuple[str, str | None]:
            captured_limits.append(kwargs.get("max_tokens"))  # type: ignore[arg-type]
            if len(captured_limits) == 1:
                return "部分语录", "length"
            return "完整语录内容", None

        with patch.object(
            analyze_transcript, "request_portraits", side_effect=fake_request
        ), patch("sys.stdin", FakeTtyStream()), patch(
            "sys.stdout", FakeTtyStream()
        ), patch("builtins.input", return_value=""):
            quotes = analyze_transcript.analyze_featured_quotes(
                "> **甲**\n>\n> 消息",
                quote_count=5,
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
            )

        self.assertEqual(quotes, "完整语录内容")
        self.assertEqual(captured_limits, [100, None])

    def test_truncated_output_ends_program_when_user_quits(self) -> None:
        """Choosing q must end the program with a clear message."""

        def fake_request(_prompt: str, **_kwargs: object) -> tuple[str, str | None]:
            return "部分语录", "length"

        with patch.object(
            analyze_transcript, "request_portraits", side_effect=fake_request
        ), patch("sys.stdin", FakeTtyStream()), patch(
            "sys.stdout", FakeTtyStream()
        ), patch("builtins.input", return_value="q"), self.assertRaisesRegex(
            SystemExit, "已按用户选择结束程序：语录精选输出被截断"
        ):
            analyze_transcript.analyze_featured_quotes(
                "> **甲**\n>\n> 消息",
                quote_count=5,
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
            )

    def test_truncated_output_raises_when_non_interactive(self) -> None:
        """Piped runs keep raising so automation does not silently hang."""

        def fake_request(_prompt: str, **_kwargs: object) -> tuple[str, str | None]:
            return "部分语录", "length"

        with patch.object(
            analyze_transcript, "request_portraits", side_effect=fake_request
        ), patch("sys.stdin", StringIO()), redirect_stdout(StringIO()):
            with self.assertRaisesRegex(RuntimeError, "当前已输出 4 字"):
                analyze_transcript.analyze_featured_quotes(
                    "> **甲**\n>\n> 消息",
                    quote_count=5,
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                )


class DiscussionMinutesTests(unittest.TestCase):
    def test_discussion_topic_limit_defaults_and_rejects_invalid_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                analyze_transcript.positive_integer_setting(
                    "LLM_MAX_DISCUSSION_TOPICS",
                    analyze_transcript.DEFAULT_MAX_DISCUSSION_TOPICS,
                ),
                5,
            )
        for value in ("0", "-1", "many"):
            with patch.dict(
                os.environ, {"LLM_MAX_DISCUSSION_TOPICS": value}, clear=False
            ), self.assertRaisesRegex(RuntimeError, "LLM_MAX_DISCUSSION_TOPICS"):
                analyze_transcript.positive_integer_setting(
                    "LLM_MAX_DISCUSSION_TOPICS", 5
                )

    def test_filters_placeholders_and_preserves_export_order(self) -> None:
        timestamp = datetime(2026, 9, 11, 9, 0)
        messages = tuple(
            contextual_analysis.TranscriptMessage(
                index, timestamp, "甲", content, content
            )
            for index, content in enumerate(
                ("[图片]", "*[消息已撤回]*", "😀", "文字 [图片]", "有效讨论"),
            )
        )

        effective = discussion_analysis.filter_discussion_messages(messages)

        self.assertEqual([item.index for item in effective], [3, 4])
        self.assertEqual([item.content for item in effective], ["文字", "有效讨论"])

    def test_segment_line_ranges_normalize_to_full_coverage(self) -> None:
        messages = (
            discussion_analysis.DiscussionMessage(11, datetime(2026, 9, 11), "甲", "甲说话"),
            discussion_analysis.DiscussionMessage(17, datetime(2026, 9, 11), "乙", "乙说话"),
            discussion_analysis.DiscussionMessage(23, datetime(2026, 9, 11), "丙", "丙说话"),
        )
        with self.assertRaisesRegex(RuntimeError, "start_line 必须是整数"):
            discussion_analysis.parse_segment_response(
                '{"topics":[{"id":"a","title":"甲","summary":"甲","substantive":true,"start_line":"1","end_line":3}]}',
                expected_messages=messages,
                namespace="test",
            )
        with self.assertRaisesRegex(RuntimeError, "实质性判定必须是布尔值"):
            discussion_analysis.parse_segment_response(
                '{"topics":[{"id":"a","title":"甲","summary":"甲","substantive":"yes","start_line":1,"end_line":3}]}',
                expected_messages=messages,
                namespace="test",
            )
        with self.assertRaisesRegex(RuntimeError, "不能重复"):
            discussion_analysis.parse_segment_response(
                '{"topics":[{"id":"a","title":"甲","summary":"甲","substantive":true,"start_line":1,"end_line":2},'
                '{"id":"a","title":"乙","summary":"乙","substantive":true,"start_line":3,"end_line":3}]}',
                expected_messages=messages,
                namespace="test",
            )
        with self.assertRaisesRegex(RuntimeError, "未包含有效区间"):
            discussion_analysis.parse_segment_response(
                '{"topics":[{"id":"a","title":"甲","summary":"甲","substantive":true,"start_line":98,"end_line":99}]}',
                expected_messages=messages,
                namespace="test",
            )
        topics = discussion_analysis.parse_segment_response(
            '{"topics":['
            '{"id":"a","title":"甲","summary":"甲","substantive":true,"start_line":2,"end_line":1},'
            '{"id":"b","title":"乙","summary":"乙","substantive":false,"start_line":2,"end_line":3},'
            '{"id":"c","title":"丙","summary":"丙","substantive":true,"start_line":99,"end_line":100}]}',
            expected_messages=messages,
            namespace="test",
        )
        self.assertEqual([topic.message_indices for topic in topics], [(11, 17, 23)])
        self.assertEqual([topic.substantive for topic in topics], [True])

        topics = discussion_analysis.parse_segment_response(
            '{"topics":['
            '{"id":"a","title":"甲","summary":"甲","substantive":true,"start_line":1,"end_line":1},'
            '{"id":"b","title":"乙","summary":"乙","substantive":false,"start_line":3,"end_line":3}]}',
            expected_messages=messages,
            namespace="test",
        )
        self.assertEqual(
            [topic.message_indices for topic in topics], [(11, 17), (23,)]
        )
        self.assertEqual([topic.substantive for topic in topics], [True, False])

        topics = discussion_analysis.parse_segment_response(
            '{"topics":[{"id":"a","title":"甲","summary":"甲","substantive":true,"start_line":2,"end_line":2}]}',
            expected_messages=messages,
            namespace="test",
        )
        self.assertEqual([topic.message_indices for topic in topics], [(11, 17, 23)])

    def test_prompt_budget_ranking_and_heat_buckets_are_deterministic(self) -> None:
        timestamp = datetime(2026, 9, 11, 9, 0)
        source = tuple(
            contextual_analysis.TranscriptMessage(
                index,
                timestamp.replace(hour=9 + index),
                "甲" if index < 2 else "乙",
                f"消息{index}",
                "",
            )
            for index in range(3)
        )
        chunks = discussion_analysis.build_segment_prompt_chunks(
            discussion_analysis.filter_discussion_messages(source), maximum_characters=500
        )
        self.assertTrue(all(len(chunk.prompt) <= 500 for chunk in chunks))

        def scripted_request(prompt: str) -> str:
            if "候选议题如下" in prompt:
                payload = prompt.split("候选议题如下：\n", 1)[1]
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "g1",
                                "title": "话题甲",
                                "summary": "摘要",
                                "source_ids": re.findall(r'"id":"([^"]+)"', payload),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if "start_line" in prompt:
                positions = [
                    int(value) for value in re.findall(r"(?m)^\[(\d+) \|", prompt)
                ]
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "a",
                                "title": "话题甲",
                                "summary": "摘要",
                                "substantive": True,
                                "start_line": positions[0],
                                "end_line": positions[-1],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            return "甲提出主题并支持继续研究。乙补充事实并总结下一步。"

        report = discussion_analysis.analyze_discussion_minutes(
            source,
            maximum_topics=5,
            maximum_input_characters=500,
            request_text=scripted_request,
        )
        self.assertEqual([topic.title for topic in report.topics], ["话题甲"])
        self.assertEqual(report.topics[0].message_count, 3)
        self.assertEqual(report.granularity, "hour")
        self.assertEqual(report.series[0].values, (1, 1, 1))
        self.assertEqual(report.series[0].color, discussion_analysis.TOPIC_COLORS[0])
        self.assertEqual(set(report.member_styles or {}), {"甲", "乙"})

    def test_discussion_requests_run_concurrently_with_identical_results(self) -> None:
        """Independent discussion requests must run in parallel without changing output."""

        timestamp = datetime(2026, 9, 11, 9, 0)
        source = tuple(
            contextual_analysis.TranscriptMessage(
                index,
                timestamp.replace(hour=9 + index),
                "甲" if index % 2 == 0 else "乙",
                f"消息{index}包含较长的讨论内容便于触发分块",
                "",
            )
            for index in range(6)
        )
        seen_threads: set[str] = set()

        def scripted_request(prompt: str) -> str:
            seen_threads.add(threading.current_thread().name)
            if "候选议题如下" in prompt:
                payload = prompt.split("候选议题如下：\n", 1)[1]
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "g1",
                                "title": "统一议题",
                                "summary": "统一后的摘要",
                                "source_ids": re.findall(r'"id":"([^"]+)"', payload),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if "start_line" in prompt:
                positions = [
                    int(value) for value in re.findall(r"(?m)^\[(\d+) \|", prompt)
                ]
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "t1",
                                "title": "话题甲",
                                "summary": "摘要",
                                "substantive": True,
                                "start_line": positions[0],
                                "end_line": positions[-1],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            return "甲提出观点并作出总结。乙补充事实并反思讨论结果。"

        report = discussion_analysis.analyze_discussion_minutes(
            source,
            maximum_topics=5,
            maximum_input_characters=500,
            request_text=scripted_request,
            maximum_workers=3,
        )
        self.assertGreater(len(seen_threads), 1)
        self.assertEqual(len(report.topics), 1)
        self.assertEqual(report.topics[0].message_count, 6)
        self.assertEqual(report.series[0].values, (1,) * 6)

    def test_merge_response_inherits_substantive_from_any_source(self) -> None:
        sources = (
            discussion_analysis.TopicCandidate("s:a", "甲题", "摘要", (1,), True),
            discussion_analysis.TopicCandidate("s:b", "乙题", "摘要", (2,), False),
            discussion_analysis.TopicCandidate("s:c", "丙题", "摘要", (3,), False),
        )
        merged = discussion_analysis.parse_merge_response(
            '{"topics":[{"id":"g1","title":"合并","summary":"摘要","source_ids":["s:a","s:b"]},'
            '{"id":"g2","title":"闲聊","summary":"摘要","source_ids":["s:c"]}]}',
            sources=sources,
            namespace="test",
        )
        self.assertEqual([item.substantive for item in merged], [True, False])

    def test_merge_response_repairs_duplicate_source_assignment(self) -> None:
        sources = (
            discussion_analysis.TopicCandidate("s:a", "甲题", "摘要", (1,), True),
            discussion_analysis.TopicCandidate("s:b", "乙题", "摘要", (2,), False),
            discussion_analysis.TopicCandidate("s:c", "丙题", "摘要", (3,), False),
        )
        output = StringIO()
        with redirect_stdout(output):
            merged = discussion_analysis.parse_merge_response(
                '{"topics":[{"id":"g1","title":"合并","summary":"摘要","source_ids":["s:a","s:b"]},'
                '{"id":"g2","title":"重复","summary":"摘要","source_ids":["s:a","s:c"]}]}',
                sources=sources,
                namespace="test",
            )
        self.assertEqual(
            [item.candidate_id for item in merged], ["test:g1", "test:g2"]
        )
        self.assertEqual(merged[0].message_indices, (1, 2))
        self.assertEqual(merged[1].message_indices, (3,))
        self.assertIn("s:a", output.getvalue())

    def test_merge_response_keeps_uncovered_candidates_as_singletons(self) -> None:
        sources = (
            discussion_analysis.TopicCandidate("s:a", "甲题", "摘要", (1,), True),
            discussion_analysis.TopicCandidate("s:b", "乙题", "摘要", (2,), False),
        )
        output = StringIO()
        with redirect_stdout(output):
            merged = discussion_analysis.parse_merge_response(
                '{"topics":[{"id":"g1","title":"合并","summary":"摘要","source_ids":["s:a"]}]}',
                sources=sources,
                namespace="test",
            )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[1].candidate_id, "test:s:b")
        self.assertEqual(merged[1].title, "乙题")
        self.assertEqual(merged[1].message_indices, (2,))
        self.assertFalse(merged[1].substantive)
        self.assertIn("s:b", output.getvalue())

    def test_merge_response_renames_duplicate_topic_ids(self) -> None:
        sources = (
            discussion_analysis.TopicCandidate("s:a", "甲题", "摘要", (1,), True),
            discussion_analysis.TopicCandidate("s:b", "乙题", "摘要", (2,), False),
        )
        output = StringIO()
        with redirect_stdout(output):
            merged = discussion_analysis.parse_merge_response(
                '{"topics":[{"id":"g1","title":"合并","summary":"摘要","source_ids":["s:a"]},'
                '{"id":"g1","title":"另一题","summary":"摘要","source_ids":["s:b"]}]}',
                sources=sources,
                namespace="test",
            )
        self.assertEqual(
            [item.candidate_id for item in merged], ["test:g1", "test:g1-2"]
        )
        self.assertIn("g1-2", output.getvalue())

    def test_excludes_unsubstantiated_topics_before_ranking(self) -> None:
        timestamp = datetime(2026, 9, 11, 9, 0)
        source = tuple(
            contextual_analysis.TranscriptMessage(
                index, timestamp.replace(hour=9 + index), "甲乙"[index % 2], f"消息{index}", ""
            )
            for index in range(4)
        )

        def scripted_request(prompt: str) -> str:
            if "候选议题如下" in prompt:
                identifiers = re.findall(r'"id":"(segment-0:[^"]+)"', prompt)
                a_id = next(item for item in identifiers if item.endswith(":a"))
                b_id = next(item for item in identifiers if item.endswith(":b"))
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "g1",
                                "title": "实质议题",
                                "summary": "摘要",
                                "source_ids": [a_id],
                            },
                            {
                                "id": "g2",
                                "title": "闲聊",
                                "summary": "摘要",
                                "source_ids": [b_id],
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            if "讨论纪要" in prompt:
                return "甲提出核心观点并总结方向。乙补充细节并反思结论。"
            return json.dumps(
                {
                    "topics": [
                        {
                            "id": "a",
                            "title": "实质议题",
                            "summary": "摘要",
                            "substantive": True,
                            "start_line": 1,
                            "end_line": 1,
                        },
                        {
                            "id": "b",
                            "title": "闲聊",
                            "summary": "摘要",
                            "substantive": False,
                            "start_line": 2,
                            "end_line": 4,
                        },
                    ]
                },
                ensure_ascii=False,
            )

        report = discussion_analysis.analyze_discussion_minutes(
            source,
            maximum_topics=5,
            maximum_input_characters=2000,
            request_text=scripted_request,
        )
        self.assertEqual([topic.title for topic in report.topics], ["实质议题"])
        self.assertEqual(report.topics[0].message_count, 1)
        self.assertEqual(len(report.series), 1)

    def test_all_unsubstantiated_topics_yield_empty_report(self) -> None:
        timestamp = datetime(2026, 9, 11, 9, 0)
        source = tuple(
            contextual_analysis.TranscriptMessage(
                index, timestamp.replace(hour=9 + index), "甲", f"消息{index}", ""
            )
            for index in range(2)
        )
        calls = iter(range(1))

        def scripted_request(_prompt: str) -> str:
            next(calls)
            return json.dumps(
                {
                    "topics": [
                        {
                            "id": "a",
                            "title": "闲聊",
                            "summary": "摘要",
                            "substantive": False,
                            "start_line": 1,
                            "end_line": 2,
                        }
                    ]
                },
                ensure_ascii=False,
            )

        report = discussion_analysis.analyze_discussion_minutes(
            source,
            maximum_topics=5,
            maximum_input_characters=2000,
            request_text=scripted_request,
        )
        self.assertEqual(report.topics, ())
        self.assertIsNone(report.chart_payload())
        self.assertEqual(
            report.to_markdown(), "## 纪要\n\n暂无可总结的有效讨论议题。"
        )

    def test_minutes_instruction_requires_thematic_concise_paragraph(self) -> None:
        partial = discussion_analysis._minutes_instruction("议题", partial=True)
        merged = discussion_analysis._minutes_instruction("议题", partial=False)
        for instruction in (partial, merged):
            self.assertIn("200~300", instruction)
            self.assertIn("核心观点、关键分歧", instruction)
            self.assertIn("虚构立场关系", instruction)
            self.assertNotIn("按消息时间顺序", instruction)
        self.assertIn("压缩归纳为一段最终纪要", merged)
        self.assertNotIn("压缩归纳为一段最终纪要", partial)

    def test_minutes_length_cap_retries_then_truncates(self) -> None:
        self.assertEqual(
            discussion_analysis.normalize_minutes("甲提出观点。**乙**总结。"),
            "甲提出观点。乙总结。",
        )
        self.assertEqual(
            discussion_analysis.normalize_minutes("甲说了一句话。乙回应一句。"),
            "甲说了一句话。乙回应一句。",
        )
        self.assertEqual(
            discussion_analysis.normalize_minutes("<<王小明>>提出观点。王小明回应。"),
            "<<王小明>>提出观点。王小明回应。",
        )
        long_text = "甲提出观点并说明理由。" * 30
        with self.assertRaisesRegex(RuntimeError, "300"):
            discussion_analysis.normalize_minutes(long_text)
        truncated = discussion_analysis.truncate_minutes(long_text)
        self.assertLessEqual(len(truncated), discussion_analysis.MAX_MINUTES_CHARACTERS)
        self.assertTrue(truncated.endswith("。"))
        self.assertEqual(
            len(discussion_analysis.truncate_minutes("甲" * 400)),
            discussion_analysis.MAX_MINUTES_CHARACTERS,
        )

        responses = [long_text, long_text, long_text, long_text]
        result = discussion_analysis._bounded_minutes(
            "任意提示", request_text=lambda _prompt: responses.pop(0)
        )
        self.assertFalse(responses)
        self.assertLessEqual(len(result), discussion_analysis.MAX_MINUTES_CHARACTERS)

    def test_bounded_minutes_rewrites_with_compression_feedback(self) -> None:
        long_text = "甲提出观点并说明理由。" * 30
        good_text = "甲提出核心观点。乙补充事实并总结结论。"
        prompts: list[str] = []

        def scripted_request(prompt: str) -> str:
            prompts.append(prompt)
            return [long_text, good_text][len(prompts) - 1]

        result = discussion_analysis._bounded_minutes(
            "任意提示", request_text=scripted_request
        )
        self.assertEqual(result, good_text)
        self.assertEqual(len(prompts), 2)
        self.assertIn("超过 300 字上限", prompts[1])
        self.assertIn("合并同类发言", prompts[1])
        self.assertIn("保留全部主要成员的核心观点与讨论结果", prompts[1])

    def test_bounded_minutes_truncates_only_after_all_rewrites_fail(self) -> None:
        long_text = "甲提出观点并说明理由。" * 30
        prompts: list[str] = []

        def scripted_request(prompt: str) -> str:
            prompts.append(prompt)
            return long_text

        result = discussion_analysis._bounded_minutes(
            "任意提示", request_text=scripted_request
        )
        self.assertEqual(len(prompts), discussion_analysis.MINUTES_REQUEST_ATTEMPTS + 1)
        self.assertLessEqual(len(result), discussion_analysis.MAX_MINUTES_CHARACTERS)

    def test_member_highlights_bold_names_and_assign_distinct_colors(self) -> None:
        moment = datetime(2026, 9, 11, 9, 0)
        report = discussion_analysis.DiscussionReport(
            (
                discussion_analysis.DiscussionTopic(
                    "t1", "议题", 2, 0, moment, moment, ("甲", "乙"),
                    "甲提出核心观点。乙补充细节并总结。",
                ),
                discussion_analysis.DiscussionTopic(
                    "t2", "议题二", 1, 5, moment, moment, ("甲",), "甲再次强调结论。"
                ),
            ),
            (),
            (),
            "day",
        )
        highlighted = report.with_member_highlights(["甲", "乙", "丙"])
        styles = highlighted.member_styles or {}

        self.assertEqual(set(styles), {"甲", "乙"})
        self.assertNotEqual(styles["甲"][0], styles["乙"][0])
        self.assertIn("**甲**提出核心观点。**乙**补充细节并总结。", highlighted.topics[0].minutes)
        self.assertEqual(highlighted.topics[1].participants, ("**甲**",))
        self.assertIn("- **主要参与者**：**甲**、**乙**", highlighted.to_markdown())
        self.assertIsNone(report.member_styles)
        self.assertEqual(
            discussion_analysis.bold_member_names("王小明和小明都在", ["小明", "王小明"]),
            "**王小明**和**小明**都在",
        )

    def test_bold_member_names_guard_ascii_boundaries_and_markers(self) -> None:
        self.assertEqual(
            discussion_analysis.bold_member_names(
                "讨论break/continue时nt认为是语法糖。", ["nt"]
            ),
            "讨论break/continue时**nt**认为是语法糖。",
        )
        self.assertEqual(
            discussion_analysis.bold_member_names("ntcn是个名字", ["nt"]),
            "ntcn是个名字",
        )
        self.assertEqual(
            discussion_analysis.bold_member_names("<<王小明>>发言。", ["王小明"]),
            "**王小明**发言。",
        )
        self.assertEqual(
            discussion_analysis.bold_member_names("<<路人>>发言。", ["王小明"]),
            "路人发言。",
        )

    def test_member_aliases_skip_conflicting_stems(self) -> None:
        self.assertEqual(
            discussion_analysis.member_aliases(["祥子(ut 不重要了健康才重要）", "nt"]),
            {"祥子": "祥子(ut 不重要了健康才重要）"},
        )
        self.assertEqual(discussion_analysis.member_aliases(["祥子(备注)", "祥子"]), {})
        self.assertEqual(discussion_analysis.member_aliases(["祥子", "祥子(备注)"]), {})
        self.assertEqual(
            discussion_analysis.member_aliases(["*new LS_Hower", "_"]),
            {"new LS_Hower": "*new LS_Hower"},
        )

    def test_member_highlights_match_alias_with_same_color(self) -> None:
        moment = datetime(2026, 9, 11, 9, 0)
        member = "祥子(ut 不重要了健康才重要）"
        report = discussion_analysis.DiscussionReport(
            (
                discussion_analysis.DiscussionTopic(
                    "t1",
                    "议题",
                    2,
                    0,
                    moment,
                    moment,
                    (member,),
                    f"祥子提出go语言观点。<<{member}>>补充细节。",
                ),
            ),
            (),
            (),
            "day",
        )
        highlighted = report.with_member_highlights([member])
        styles = highlighted.member_styles or {}

        self.assertEqual(styles["祥子"], styles[member])
        self.assertIn("**祥子**提出go语言观点。", highlighted.topics[0].minutes)
        self.assertIn(f"**{discussion_analysis.escape_inline_name(member)}**补充细节。", highlighted.topics[0].minutes)

    def test_bold_member_names_escape_markdown_and_html_characters(self) -> None:
        self.assertEqual(
            discussion_analysis.bold_member_names("*new LS_Hower发言。", ["*new LS_Hower"]),
            "**\\*new LS\\_Hower**发言。",
        )
        self.assertEqual(
            discussion_analysis.bold_member_names("由_提出观点。", ["_"]),
            "由**\\_**提出观点。",
        )
        self.assertEqual(
            discussion_analysis.bold_member_names("<b>老哥</b>发言。", ["<b>老哥"]),
            "**&lt;b&gt;老哥**</b>发言。",
        )
        self.assertEqual(
            discussion_analysis.bold_member_names("<<*new LS_Hower>>总结。", ["*new LS_Hower"]),
            "**\\*new LS\\_Hower**总结。",
        )

    def test_member_highlights_style_names_with_special_characters(self) -> None:
        moment = datetime(2026, 9, 11, 9, 0)
        report = discussion_analysis.DiscussionReport(
            (
                discussion_analysis.DiscussionTopic(
                    "t1", "议题", 2, 0, moment, moment, ("*new LS_Hower",),
                    "*new LS_Hower分享教程。",
                ),
            ),
            (),
            (),
            "day",
        )
        highlighted = report.with_member_highlights(["*new LS_Hower"])

        self.assertEqual(
            set(highlighted.member_styles or {}),
            {"*new LS_Hower", "new LS_Hower"},
        )
        self.assertIn(
            "**\\*new LS\\_Hower**分享教程。", highlighted.topics[0].minutes
        )

    def test_normalize_minutes_strips_single_asterisks(self) -> None:
        self.assertEqual(
            discussion_analysis.normalize_minutes("甲提出观点*强调语气。乙回应*补充。"),
            "甲提出观点强调语气。乙回应补充。",
        )
        self.assertEqual(
            discussion_analysis.normalize_minutes("<<*new LS_Hower>>提出观点。乙回应。"),
            "<<*new LS_Hower>>提出观点。乙回应。",
        )

    def test_topic_colors_stay_distinct_beyond_palette(self) -> None:
        palette = discussion_analysis.TOPIC_COLORS
        self.assertEqual(
            [discussion_analysis.topic_color(index) for index in range(len(palette))],
            list(palette),
        )
        colors = [discussion_analysis.topic_color(index) for index in range(12)]
        self.assertEqual(len(set(colors)), 12)
        self.assertEqual(
            colors,
            [discussion_analysis.topic_color(index) for index in range(12)],
        )

    def test_segment_refusal_isolates_to_skipped_fragment(self) -> None:
        """A refused chunk must not abort the other segments' topic detection."""

        timestamp = datetime(2026, 9, 11, 9, 0)
        source = tuple(
            contextual_analysis.TranscriptMessage(
                index, timestamp.replace(hour=9 + index), "甲", f"消息{index}内容", ""
            )
            for index in range(6)
        )

        def scripted_request(prompt: str) -> str:
            if "候选议题如下" in prompt:
                payload = prompt.split("候选议题如下：\n", 1)[1]
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "g1",
                                "title": "有效讨论",
                                "summary": "摘要",
                                "source_ids": re.findall(r'"id":"([^"]+)"', payload),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if "讨论纪要" in prompt:
                return "甲提出观点并作出总结。乙补充事实并反思讨论结果。"
            if "start_line" in prompt:
                if "消息3" in prompt:
                    return "抱歉，我无法回答这个问题。"
                positions = [
                    int(value) for value in re.findall(r"(?m)^\[(\d+) \|", prompt)
                ]
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "t1",
                                "title": "有效讨论",
                                "summary": "摘要",
                                "substantive": True,
                                "start_line": positions[0],
                                "end_line": positions[-1],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            raise AssertionError(f"未预期的请求：{prompt[:60]}")

        report = discussion_analysis.analyze_discussion_minutes(
            source,
            maximum_topics=5,
            maximum_input_characters=500,
            request_text=scripted_request,
        )
        chunks = discussion_analysis.build_segment_prompt_chunks(
            discussion_analysis.filter_discussion_messages(source),
            maximum_characters=500,
        )
        refused_total = sum(
            len(chunk.messages) for chunk in chunks if "消息3" in chunk.prompt
        )
        self.assertGreater(refused_total, 0)
        self.assertEqual([topic.title for topic in report.topics], ["有效讨论"])
        self.assertEqual(
            sum(topic.message_count for topic in report.topics), 6 - refused_total
        )

    def test_topic_minutes_failure_keeps_topic_entry(self) -> None:
        """A refused minutes request degrades one topic instead of the whole section."""

        timestamp = datetime(2026, 9, 11, 9, 0)
        source = tuple(
            contextual_analysis.TranscriptMessage(
                index, timestamp.replace(hour=9 + index), "甲", f"消息{index}内容", ""
            )
            for index in range(2)
        )

        def scripted_request(prompt: str) -> str:
            if "候选议题如下" in prompt:
                payload = prompt.split("候选议题如下：\n", 1)[1]
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "g1",
                                "title": "有效讨论",
                                "summary": "摘要",
                                "source_ids": re.findall(r'"id":"([^"]+)"', payload),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if "讨论纪要" in prompt:
                return "抱歉，我无法回答这个问题。"
            positions = [
                int(value) for value in re.findall(r"(?m)^\[(\d+) \|", prompt)
            ]
            return json.dumps(
                {
                    "topics": [
                        {
                            "id": "t1",
                            "title": "有效讨论",
                            "summary": "摘要",
                            "substantive": True,
                            "start_line": positions[0],
                            "end_line": positions[-1],
                        }
                    ]
                },
                ensure_ascii=False,
            )

        report = discussion_analysis.analyze_discussion_minutes(
            source,
            maximum_topics=5,
            maximum_input_characters=2000,
            request_text=scripted_request,
        )
        self.assertEqual(len(report.topics), 1)
        self.assertEqual(report.topics[0].message_count, 2)
        self.assertEqual(
            report.topics[0].minutes, "该议题的纪要生成失败，未能概括讨论内容。"
        )

    def test_fallback_llm_setting_requires_complete_configuration(self) -> None:
        """The fallback model is either fully configured or disabled entirely."""

        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(analyze_transcript.fallback_llm_setting())
        with patch.dict(
            os.environ,
            {
                "LLM_FALLBACK_BASE_URL": "https://fallback.test/v1",
                "LLM_FALLBACK_MODEL": "fallback-model",
                "LLM_FALLBACK_API_KEY": "fallback-key",
            },
            clear=True,
        ):
            self.assertEqual(
                analyze_transcript.fallback_llm_setting(),
                ("https://fallback.test/v1", "fallback-model", "fallback-key"),
            )
        for partial in (
            {"LLM_FALLBACK_BASE_URL": "https://fallback.test/v1"},
            {
                "LLM_FALLBACK_BASE_URL": "https://fallback.test/v1",
                "LLM_FALLBACK_MODEL": "fallback-model",
            },
        ):
            with patch.dict(os.environ, partial, clear=True), self.assertRaisesRegex(
                RuntimeError, "备用模型配置不完整"
            ):
                analyze_transcript.fallback_llm_setting()

    @staticmethod
    def _discussion_transcript() -> str:
        blocks = []
        for offset in range(2):
            blocks.append(
                f"## 2026-09-11 19:00:{offset:02d} · 测试群\n\n"
                f"> **甲**\n>\n> 讨论消息{offset}"
            )
        return "\n\n".join(blocks)

    def test_discussion_content_filter_falls_back_to_backup_model(self) -> None:
        """A moderation-refused discussion request is retried on the fallback model."""

        models_used: list[str | None] = []

        def fake_request(prompt: str, **kwargs: object) -> tuple[str, str | None]:
            model = kwargs.get("model")
            models_used.append(model if isinstance(model, str) else None)
            if "start_line" in prompt:
                if model == "test-model":
                    return "", "content_filter"
                positions = [
                    int(value) for value in re.findall(r"(?m)^\[(\d+) \|", prompt)
                ]
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "t1",
                                "title": "统一议题",
                                "summary": "摘要",
                                "substantive": True,
                                "start_line": positions[0],
                                "end_line": positions[-1],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ), None
            if "讨论纪要" in prompt:
                return "甲提出核心观点并总结方向。乙补充事实并反思结论。", None
            if "群像速览" in prompt:
                return "- **整体画像**：测试概括。", None
            member = re.search(r"(?m)^- (\S+)（本批次", prompt)
            if member:
                return f"### {member.group(1)}\n- **角色定位**：测试标签", None
            return "", None

        with patch.object(
            analyze_transcript, "request_portraits", side_effect=fake_request
        ), patch.object(
            analyze_transcript, "analyze_featured_quotes", return_value="暂无。"
        ):
            output = StringIO()
            with redirect_stdout(output):
                analysis = analyze_transcript.analyze_all_members(
                    self._discussion_transcript(),
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    members_per_request=2,
                    max_input_characters=1_000,
                    top_members=None,
                    min_message_count=0,
                    fallback_llm=(
                        "https://fallback.test/v1",
                        "fallback-model",
                        "fallback-key",
                    ),
                )

        self.assertIn("test-model", models_used)
        self.assertIn("fallback-model", models_used)
        self.assertIn("降级到备用模型 fallback-model", output.getvalue())
        self.assertIn("统一议题", analysis.markdown)
        self.assertIn("提出核心观点并总结方向", analysis.markdown)

    def test_discussion_content_filter_without_fallback_degrades_to_unavailable(
        self,
    ) -> None:
        """Without a fallback model, a refused merge still degrades the whole minutes."""

        def fake_request(prompt: str, **_kwargs: object) -> tuple[str, str | None]:
            if "start_line" in prompt:
                return json.dumps(
                    {
                        "topics": [
                            {
                                "id": "t1",
                                "title": "话题甲",
                                "summary": "摘要",
                                "substantive": True,
                                "start_line": 1,
                                "end_line": 1,
                            },
                            {
                                "id": "t2",
                                "title": "话题乙",
                                "summary": "摘要",
                                "substantive": True,
                                "start_line": 2,
                                "end_line": 2,
                            },
                        ]
                    },
                    ensure_ascii=False,
                ), None
            if "候选议题如下" in prompt:
                return "", "content_filter"
            member = re.search(r"(?m)^- (\S+)（本批次", prompt)
            if member:
                return f"### {member.group(1)}\n- **角色定位**：测试标签", None
            if "群像速览" in prompt:
                return "- **整体画像**：测试概括。", None
            return "", None

        with patch.object(
            analyze_transcript, "request_portraits", side_effect=fake_request
        ), patch.object(
            analyze_transcript, "analyze_featured_quotes", return_value="暂无。"
        ):
            output = StringIO()
            with redirect_stdout(output):
                analysis = analyze_transcript.analyze_all_members(
                    self._discussion_transcript(),
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    members_per_request=2,
                    max_input_characters=1_000,
                    top_members=None,
                    min_message_count=0,
                )

        self.assertIn("讨论纪要生成失败", output.getvalue())
        self.assertIn("纪要暂不可用", analysis.markdown)
        self.assertNotIn("fallback-model", output.getvalue())

    def test_segment_window_bounds_classification_chunks(self) -> None:
        """Segment prompts stay in a window where exact id coverage stays reliable."""

        timestamp = datetime(2026, 9, 11, 9, 0)
        source = tuple(
            contextual_analysis.TranscriptMessage(
                index, timestamp, f"成员{index}", f"第{index}条讨论内容" * 10, ""
            )
            for index in range(120)
        )
        chunks = discussion_analysis.build_segment_prompt_chunks(
            discussion_analysis.filter_discussion_messages(source),
            maximum_characters=50_000,
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(
            all(
                len(chunk.prompt) <= discussion_analysis.SEGMENT_WINDOW_CHARACTERS
                for chunk in chunks
            )
        )

    def test_retry_sends_corrective_feedback(self) -> None:
        """The single retry must tell the model exactly which check failed."""

        prompts: list[str] = []
        responses = iter(("bad", "good"))

        def parser(response: str) -> str:
            if response == "bad":
                raise RuntimeError("start_line 必须是整数")
            return response

        with self.assertRaisesRegex(RuntimeError, "start_line 必须是整数"):
            discussion_analysis._validated_request(
                "原始提示",
                request_text=lambda prompt: (prompts.append(prompt), "bad")[1],
                parser=parser,
            )
        result = discussion_analysis._validated_request(
            "原始提示",
            request_text=lambda prompt: (prompts.append(prompt), next(responses))[1],
            parser=parser,
        )

        self.assertEqual(result, "good")
        self.assertEqual(len(prompts), 4)
        self.assertTrue(prompts[1].startswith("原始提示"))
        self.assertIn("start_line 必须是整数", prompts[1])
        self.assertIn("重新完整输出", prompts[1])

    def test_rejection_error_carries_response_head(self) -> None:
        """Refusal texts must surface in errors so the trigger can be reviewed."""

        messages = (
            discussion_analysis.DiscussionMessage(1, datetime(2026, 9, 11), "甲", "甲说话"),
            discussion_analysis.DiscussionMessage(2, datetime(2026, 9, 11), "乙", "乙说话"),
        )
        with self.assertRaisesRegex(RuntimeError, "响应开头：抱歉，我无法回答这个问题"):
            discussion_analysis.parse_segment_response(
                "抱歉，我无法回答这个问题。",
                expected_messages=messages,
                namespace="test",
            )

    def test_refusal_trace_dump_uses_timestamped_name(self) -> None:
        """Refused requests land in temp/ as YYYYmmddHHMMSS_illegal.md."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            pattern = re.compile(r"^\d{14}_illegal(?:_\d+)?\.md$")
            discussion_analysis.write_refusal_trace(
                3,
                prompt="完整请求内容",
                response="抱歉，我无法回答这个问题。",
                message_ids=(31, 45),
                error="讨论议题响应不是 JSON 对象（响应开头：抱歉）",
                directory=directory,
            )
            discussion_analysis.write_refusal_trace(
                4,
                prompt="第二份完整请求内容",
                response="抱歉，您的问题我无法识别。",
                message_ids=(46, 58),
                error="讨论议题响应不是 JSON 对象（响应开头：抱歉）",
                directory=directory,
            )

            dumps = sorted(path for path in directory.iterdir() if pattern.match(path.name))
            self.assertEqual(len(dumps), 2)
            first_text = dumps[0].read_text(encoding="utf-8")
            self.assertIn("完整请求内容", first_text)
            self.assertIn("抱歉，我无法回答这个问题。", first_text)
            self.assertIn("31–45", first_text)
            second_text = dumps[1].read_text(encoding="utf-8")
            self.assertIn("第二份完整请求内容", second_text)

    def test_thinking_control_setting_accepts_only_enabled_or_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(analyze_transcript.thinking_control_setting())
        with patch.dict(os.environ, {"LLM_THINKING": " disabled "}):
            self.assertEqual(
                analyze_transcript.thinking_control_setting(),
                {"type": "disabled"},
            )
        with patch.dict(os.environ, {"LLM_THINKING": "ENABLED"}):
            self.assertEqual(
                analyze_transcript.thinking_control_setting(),
                {"type": "enabled"},
            )
        with patch.dict(os.environ, {"LLM_THINKING": "off"}):
            with self.assertRaisesRegex(RuntimeError, "LLM_THINKING"):
                analyze_transcript.thinking_control_setting()

    def test_request_payload_carries_thinking_field_only_when_configured(self) -> None:
        """The thinking field must be injected from LLM_THINKING and omitted otherwise."""

        def request_with_payload(trace_dir: Path) -> dict[str, object]:
            response = MagicMock()
            response.json.return_value = {"choices": [{"message": {"content": "结果"}}]}
            with (
                patch.object(analyze_transcript.httpx, "Client") as client_class,
                patch.object(analyze_transcript, "TRACE_DIRECTORY", trace_dir),
                patch.dict(os.environ, {"LLM_STREAM": "0"}),
            ):
                client = client_class.return_value.__enter__.return_value
                client.post.return_value = response
                analyze_transcript.request_portraits(
                    "测试请求",
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    progress_interval_seconds=0,
                )
                return client.post.call_args.kwargs["json"]

        with tempfile.TemporaryDirectory() as trace_dir:
            with patch.dict(os.environ, {"LLM_THINKING": "disabled"}):
                payload = request_with_payload(Path(trace_dir))
            self.assertEqual(payload["thinking"], {"type": "disabled"})

            with patch.dict(os.environ, {}, clear=True):
                payload = request_with_payload(Path(trace_dir))
            self.assertNotIn("thinking", payload)


class HtmlRenderingTests(unittest.TestCase):
    def test_footer_only_shows_timezone_free_generation_time(self) -> None:
        """Generated HTML must not expose its source path or model configuration."""

        rendered = analyze_transcript.render_html("# 群员画像分析")

        self.assertIn("生成时间：", rendered)
        self.assertNotIn("消息记录：", rendered)
        self.assertNotIn("模型：<code>", rendered)
        self.assertNotIn("中国标准时间", rendered)

    def test_uses_the_chat_name_and_card_enhancement_script(self) -> None:
        """The standalone report carries its context and enhances member sections."""

        rendered = analyze_transcript.render_html(
            "## 群像速览\n\n- **分析成员**：1 位\n\n---\n\n### 甲\n- **活跃度**：3 条（60%），晚间为主。",
            chat_name="测试群",
        )

        self.assertIn("测试群 · 群员画像", rendered)
        self.assertIn("member-card", rendered)

    def test_discussion_chart_is_structured_before_portrait_cards(self) -> None:
        report = discussion_analysis.DiscussionReport(
            (
                discussion_analysis.DiscussionTopic(
                    "t1", "测试议题", 2, 0, datetime(2026, 9, 11, 9),
                    datetime(2026, 9, 11, 10), ("甲",), "甲提出观点并作出总结。乙补充信息并支持讨论。"
                ),
            ),
            ("09-11 09:00", "09-11 10:00"),
            (discussion_analysis.HeatSeries("t1", "测试议题", "#36718a", (1, 1)),),
            "hour",
        )
        markdown = analyze_transcript.build_analysis_document(
            member_count=1,
            portraits=("### 甲\n- **活跃度**：2 条（100%），晚间为主。",),
            discussion_minutes=report.to_markdown(),
        )
        rendered = analyze_transcript.render_html(markdown, discussion=report)

        self.assertLess(markdown.index("## 纪要"), markdown.index("## 用户画像"))
        self.assertNotIn("讨论热度", markdown)
        self.assertIn("echarts@5.5.1", rendered)
        self.assertIn("discussion-topic", rendered)
        self.assertIn("__qqstalkerDiscussionChartState", rendered)
        self.assertIn("member-name", rendered)
        self.assertNotIn("discussion-fallback", rendered)
        self.assertIn("activity-bar", rendered)

    def test_member_highlight_styles_reach_payload_and_markdown(self) -> None:
        base = discussion_analysis.DiscussionReport(
            (
                discussion_analysis.DiscussionTopic(
                    "t1", "测试议题", 2, 0, datetime(2026, 9, 11, 9),
                    datetime(2026, 9, 11, 10), ("甲", "乙"),
                    "甲提出核心观点。乙补充信息并作出总结。",
                ),
            ),
            ("09-11 09:00", "09-11 10:00"),
            (discussion_analysis.HeatSeries("t1", "测试议题", "#0072B2", (1, 1)),),
            "hour",
        )
        report = base.with_member_highlights(["甲", "乙", "丙"])
        markdown = analyze_transcript.build_analysis_document(
            member_count=2,
            portraits=("### 甲\n- **活跃度**：2 条（100%），晚间为主。",),
            discussion_minutes=report.to_markdown(),
        )
        rendered = analyze_transcript.render_html(markdown, discussion=report)

        self.assertIn("**甲**提出核心观点", markdown)
        self.assertIn("**主要参与者**：**甲**、**乙**", markdown)
        styles = report.member_styles or {}
        self.assertEqual(set(styles), {"甲", "乙"})
        self.assertIn('{"甲": {"color": "hsl(0, 65%, 27%)"', rendered)
        self.assertIn('"background": "hsl(0, 70%, 90%)"', rendered)
        self.assertIn("member-name", rendered)

    def test_includes_role_and_featured_quote_enhancements(self) -> None:
        """Cards and quote curation receive their own visual hierarchy hooks."""

        rendered = analyze_transcript.render_html(
            """### 甲
- **角色定位**：资料推荐、话题引导

## 语录精选

### 甲
> 这也太逆天了

- **点评**：荒诞反差强烈。"""
        )

        self.assertIn("member-role", rendered)
        self.assertIn("featured-quote", rendered)
        self.assertIn("role.remove()", rendered)

    def test_extracts_group_name_from_transcript_heading(self) -> None:
        """The direct HTML CLI can title its report from an export heading."""

        chat_name = analyze_transcript.extract_chat_name(
            "## 2026-09-11 09:00:00 · 测试群\n\n> **甲**\n>\n> 消息"
        )

        self.assertEqual(chat_name, "测试群")

    def test_directory_output_uses_the_group_name_when_provided(self) -> None:
        """The pipeline can use the selected chat name in its cached HTML filename."""

        output_path = analyze_transcript.resolve_output_path(
            Path("analysis"),
            generated_at=datetime(2026, 9, 11, 8, 30, 45),
            chat_name="测试群",
        )

        self.assertEqual(output_path, Path("analysis/20260911083045_测试群.html"))


class PortraitNormalizationTests(unittest.TestCase):
    def test_replaces_verbose_activity_with_exact_message_count_and_removes_title(self) -> None:
        """Model-added portrait titles and qualitative activity prose must not reach HTML."""

        normalized = analyze_transcript.normalize_member_portraits(
            """# 成员画像

### 甲
- **活跃度**：本批次消息量极高，几乎全天持续刷屏式发言。
- **互动与表达**：表达直接。

## 成员画像

### 乙
- **活跃度**：发言不多但很活跃。
- **互动与表达**：回复简短。""",
            (("甲", ["a"] * 12), ("乙", ["b"] * 3)),
            total_message_count=20,
        )

        self.assertNotIn("成员画像", normalized)
        self.assertIn("- **活跃度**：12 条（60%），时段未知。", normalized)
        self.assertIn("- **活跃度**：3 条（15%），时段未知。", normalized)


class ActivitySummaryTests(unittest.TestCase):
    def test_summarizes_count_percentage_and_peak_time_period(self) -> None:
        """Activity must report the member's count, share, and a concise peak period."""

        summary = analyze_transcript.build_activity_summary(
            [
                "## 2026-09-11 19:05:00 · 测试群\n\n> **甲**\n>\n> 消息",
                "## 2026-09-11 21:15:00 · 测试群\n\n> **甲**\n>\n> 消息",
                "## 2026-09-11 07:20:00 · 测试群\n\n> **甲**\n>\n> 消息",
            ],
            total_message_count=5,
        )

        self.assertEqual(summary, "3 条（60%），晚间为主。")


class ArgumentParserTests(unittest.TestCase):
    def test_accepts_member_selection_and_quote_options(self) -> None:
        """The CLI must expose member selection and featured-quote controls."""

        args = analyze_transcript.build_argument_parser().parse_args(
            [
                "input.md",
                "output.html",
                "--top-members",
                "8",
                "--min-message-count",
                "3",
                "--quote-count",
                "5",
            ]
        )

        self.assertEqual(args.top_members, 8)
        self.assertEqual(args.min_message_count, 3)
        self.assertEqual(args.quote_count, 5)


class LlmErrorTests(unittest.TestCase):
    def test_recognizes_only_transient_http_statuses_as_retryable(self) -> None:
        """Retries should target temporary service failures, not invalid requests."""

        for status_code in (408, 409, 425, 429, 500, 503, 599):
            self.assertTrue(analyze_transcript.is_retryable_llm_status(status_code))
        for status_code in (400, 401, 403, 404, 422):
            self.assertFalse(analyze_transcript.is_retryable_llm_status(status_code))

    def test_retries_a_read_timeout_then_returns_the_response(self) -> None:
        """A transient transport timeout should retry with exponential backoff."""

        request = httpx.Request("POST", "https://example.test/chat/completions")
        response = MagicMock()
        response.json.return_value = {
            "choices": [
                {"message": {"content": "### 甲\n- **角色定位**：活跃成员"}}
            ]
        }
        with (
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.object(analyze_transcript.time, "sleep") as sleep,
            patch.dict(os.environ, {"LLM_STREAM": "0"}),
        ):
            client = client_class.return_value.__enter__.return_value
            client.post.side_effect = [httpx.ReadTimeout("timed out", request=request), response]

            content, finish_reason = analyze_transcript.request_portraits(
                "测试请求",
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=100,
                timeout_seconds=1,
                max_retries=2,
                retry_delay_seconds=0.25,
            )

        self.assertIn("### 甲", content)
        self.assertIsNone(finish_reason)
        self.assertEqual(client.post.call_count, 2)
        sleep.assert_called_once_with(0.25)

    def test_does_not_retry_an_invalid_credentials_response(self) -> None:
        """Permanent 4xx failures should remain actionable and return immediately."""

        request = httpx.Request("POST", "https://example.test/chat/completions")
        response = httpx.Response(401, text="invalid credentials", request=request)
        with (
            patch.object(analyze_transcript.httpx, "Client") as client_class,
            patch.object(analyze_transcript.time, "sleep") as sleep,
            patch.dict(os.environ, {"LLM_STREAM": "0"}),
        ):
            client = client_class.return_value.__enter__.return_value
            client.post.return_value = response

            with self.assertRaisesRegex(RuntimeError, "HTTP 401.*invalid credentials"):
                analyze_transcript.request_portraits(
                    "测试请求",
                    base_url="https://example.test",
                    model="test-model",
                    api_key="test-key",
                    max_tokens=100,
                    timeout_seconds=1,
                    max_retries=2,
                    retry_delay_seconds=0.25,
                )

        self.assertEqual(client.post.call_count, 1)
        sleep.assert_not_called()

    def test_explains_unsupported_ark_agent_plan_model(self) -> None:
        """Ark Plan incompatibility must point to the compatible configuration choices."""

        message = analyze_transcript.format_llm_http_error(
            base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
            status_code=404,
            detail='{"error":{"code":"UnsupportedModel"}}',
        )

        self.assertIn("Agent Plan", message)
        self.assertIn("https://ark.cn-beijing.volces.com/api/v3", message)
        self.assertNotIn("UnsupportedModel", message)

    def test_keeps_non_plan_errors_intact(self) -> None:
        """Unrelated provider errors must retain their diagnostic response body."""

        message = analyze_transcript.format_llm_http_error(
            base_url="https://api.openai.com/v1",
            status_code=401,
            detail="invalid credentials",
        )

        self.assertEqual(message, "大模型请求失败（HTTP 401）：invalid credentials")


class ContextualAnalysisTests(unittest.TestCase):
    """The contextual prompt builder must remain ordered, bounded, and conservative."""

    TRANSCRIPT = """## 2026-09-11 09:00:00 · 测试群

> **乙**
>
> 先提出一个问题

## 2026-09-11 09:01:00 · 测试群

> **甲**
>
> @乙 我来回答？

## 2026-09-11 09:02:00 · 测试群

> **Q群管家**
>
> 系统通知

## 2026-09-11 09:03:00 · 测试群

> **丙**
>
> 这是后续补充

## 2026-09-11 09:04:00 · 测试群

> **甲**
>
> [图片]
"""

    def test_parses_human_messages_in_order_and_excludes_service_senders(self) -> None:
        messages = contextual_analysis.parse_messages(
            self.TRANSCRIPT, excluded_members=analyze_transcript.EXCLUDED_MEMBER_NAMES
        )

        self.assertEqual([message.member for message in messages], ["乙", "甲", "丙", "甲"])
        self.assertEqual([message.index for message in messages], [0, 1, 2, 3])
        self.assertEqual(messages[1].content, "@乙 我来回答？")

    def test_prioritizes_direct_signal_and_merges_windows_without_duplicates(self) -> None:
        messages = contextual_analysis.parse_messages(
            self.TRANSCRIPT, excluded_members=analyze_transcript.EXCLUDED_MEMBER_NAMES
        )
        centers = contextual_analysis.choose_representatives(
            tuple(message for message in messages if message.member == "甲"), maximum=2
        )
        window = contextual_analysis.merged_window_messages(
            messages, centers, before=1, after=1
        )

        self.assertEqual([message.index for message in centers], [1])
        self.assertEqual([message.index for message in window], [0, 1, 2])

    def test_serialization_marks_and_truncates_context(self) -> None:
        messages = contextual_analysis.parse_messages(
            self.TRANSCRIPT, excluded_members=analyze_transcript.EXCLUDED_MEMBER_NAMES
        )
        centers = (messages[1],)
        rendered = contextual_analysis.serialize_context(
            messages[:3], centers, maximum_characters=100
        )

        self.assertIn("[2 | 2026-09-11 09:01:00 | 甲]", rendered)
        self.assertIn("内容已截断", rendered)
        self.assertLessEqual(len(rendered), 100)

    def test_allows_one_sided_context_but_rejects_two_zero_sides(self) -> None:
        self.assertEqual(analyze_transcript.nonnegative_integer_setting("MISSING", 0), 0)
        with self.assertRaisesRegex(RuntimeError, "不能同时为 0"):
            analyze_transcript.analyze_all_members(
                self.TRANSCRIPT,
                base_url="https://example.test",
                model="test-model",
                api_key="test-key",
                max_tokens=10,
                timeout_seconds=1,
                members_per_request=2,
                max_input_characters=10_000,
                top_members=1,
                min_message_count=0,
                context_messages_before=0,
                context_messages_after=0,
            )


if __name__ == "__main__":
    unittest.main()
