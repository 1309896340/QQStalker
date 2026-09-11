"""Tests for transcript member selection and portrait-document assembly."""

import unittest

from src import analyze_transcript


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


class PromptTests(unittest.TestCase):
    def test_requires_an_unlabeled_quoted_viewpoint_for_each_member(self) -> None:
        """Portrait prompts must use a standalone quote instead of a labeled field."""

        prompt = analyze_transcript.build_member_prompt((("甲", ["一条消息"]),))

        self.assertNotIn("**精选语录**", prompt)
        self.assertIn("> **“最具代表性的具体观点或语录", prompt)
        self.assertIn("不超过 25 字", prompt)
        self.assertIn("忠实的简短转述", prompt)
        self.assertNotIn("互动与表达", prompt)

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


class HtmlRenderingTests(unittest.TestCase):
    def test_footer_only_shows_timezone_free_generation_time(self) -> None:
        """Generated HTML must not expose its source path or model configuration."""

        rendered = analyze_transcript.render_html("# 群员画像分析")

        self.assertIn("生成时间：", rendered)
        self.assertNotIn("消息记录：", rendered)
        self.assertNotIn("模型：<code>", rendered)
        self.assertNotIn("中国标准时间", rendered)


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


if __name__ == "__main__":
    unittest.main()
