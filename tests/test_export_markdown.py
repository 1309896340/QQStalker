"""Tests for group-filtered Markdown exports."""

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.qqstalker_cli import export_markdown


class ArgumentParserTests(unittest.TestCase):
    def test_requires_a_group_name(self) -> None:
        """The group name is a required positional argument."""

        args = export_markdown.build_argument_parser().parse_args(
            ["2026-09-11", "测试群", "output"]
        )

        self.assertEqual(args.chat_name, "测试群")


class GroupValidationTests(unittest.TestCase):
    def test_rejects_an_unknown_group_before_querying_messages_or_writing_output(self) -> None:
        """An invalid group name must stop the export before later workflow steps."""

        session = MagicMock()
        session.exec.return_value.first.return_value = None
        session_context = MagicMock()
        session_context.__enter__.return_value = session

        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "exports"
            with (
                patch("src.qqstalker_cli.export_markdown.create_database_engine", return_value=object()),
                patch("src.qqstalker_cli.export_markdown.Session", return_value=session_context),
            ):
                with self.assertRaisesRegex(ValueError, "群名不存在：不存在的群"):
                    export_markdown.export_markdown(
                        date(2026, 9, 11),
                        date(2026, 9, 11),
                        "不存在的群",
                        output_dir,
                        timezone=ZoneInfo("Asia/Shanghai"),
                    )

            self.assertEqual(session.exec.call_count, 1)
            self.assertFalse(output_dir.exists())
