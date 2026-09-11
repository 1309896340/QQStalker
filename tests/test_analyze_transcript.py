"""Tests for transcript member selection and portrait-document assembly."""

import unittest

from src import analyze_transcript


class SelectMembersTests(unittest.TestCase):
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

    def test_keeps_source_order_when_top_members_is_omitted(self) -> None:
        """Filtering alone must retain the transcript's first-seen member order."""

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

        self.assertEqual([member for member, _ in selected], ["甲", "乙", "丙"])


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
    def test_accepts_member_selection_options(self) -> None:
        """The CLI must expose both member-selection controls to users."""

        args = analyze_transcript.build_argument_parser().parse_args(
            [
                "input.md",
                "output.html",
                "--top-members",
                "8",
                "--min-message-count",
                "3",
            ]
        )

        self.assertEqual(args.top_members, 8)
        self.assertEqual(args.min_message_count, 3)


if __name__ == "__main__":
    unittest.main()
