"""Tests for transcript member selection and portrait-document assembly."""

from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from src.qqstalker_cli import analyze_transcript


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
        self.assertIn("成员名称", prompt)
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

        self.assertIn("**分析成员**：1 位", analysis)
        self.assertNotIn("**分析成员**：2 位", analysis)


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
        self.assertIn("activity-bar", rendered)

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


if __name__ == "__main__":
    unittest.main()
