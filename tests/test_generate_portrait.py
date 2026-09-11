"""Tests for the all-in-one portrait generation command."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src import generate_portrait


class ArgumentParserTests(unittest.TestCase):
    def test_defaults_cover_each_linked_command(self) -> None:
        """The combined CLI keeps the component CLIs' default behavior."""

        args = generate_portrait.build_argument_parser().parse_args(
            ["2026-09-11", "output"]
        )

        self.assertIsNone(args.end_date)
        self.assertEqual(args.timezone, "Asia/Shanghai")
        self.assertEqual(args.env_file, Path(".env"))
        self.assertIsNone(args.top_members)
        self.assertEqual(args.min_message_count, 0)
        self.assertIsNone(args.dpi)
        self.assertEqual(args.width_mm, 210.0)
        self.assertIsNone(args.max_height_px)
        self.assertFalse(args.stitch_horizontal)
        self.assertEqual(args.stitch_count, 4)

    def test_accepts_all_optional_parameters(self) -> None:
        """Every component CLI option is available from the combined command."""

        args = generate_portrait.build_argument_parser().parse_args(
            [
                "2026-09-11",
                "output",
                "--end-date",
                "2026-09-12",
                "--timezone",
                "Asia/Hong_Kong",
                "--env-file",
                "settings.env",
                "--top-members",
                "10",
                "--min-message-count",
                "2",
                "--dpi",
                "144",
                "--width-mm",
                "180",
                "--max-height-px",
                "12000",
                "--stitch-horizontal",
                "--stitch-count",
                "3",
            ]
        )

        self.assertEqual(args.end_date.isoformat(), "2026-09-12")
        self.assertEqual(args.timezone, "Asia/Hong_Kong")
        self.assertEqual(args.env_file, Path("settings.env"))
        self.assertEqual(args.top_members, 10)
        self.assertEqual(args.min_message_count, 2)
        self.assertEqual(args.dpi, 144)
        self.assertEqual(args.width_mm, 180.0)
        self.assertEqual(args.max_height_px, 12000)
        self.assertTrue(args.stitch_horizontal)
        self.assertEqual(args.stitch_count, 3)


class GeneratePortraitTests(unittest.TestCase):
    def test_caches_html_and_keeps_only_markdown_temporary(self) -> None:
        """The transcript is temporary while the analysis HTML remains with the PNG output."""

        captured: dict[str, object] = {}

        def export_markdown(*args: object, **kwargs: object) -> Path:
            output_dir = args[2]
            assert isinstance(output_dir, Path)
            markdown_path = output_dir / "transcript.md"
            markdown_path.write_text("# QQ 消息记录\n", encoding="utf-8")
            captured["markdown_path"] = markdown_path
            return markdown_path

        def render_html_to_pngs(input_html: Path, output_path: Path, **kwargs: object) -> list[Path]:
            captured["html_path"] = input_html
            captured["output_path"] = output_path
            captured["render_options"] = kwargs
            self.assertTrue(input_html.is_file())
            return [output_path]

        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "output"
            with (
                patch("src.generate_portrait.analyze_transcript.load_dotenv"),
                patch(
                    "src.generate_portrait.analyze_transcript.required_setting",
                    side_effect=lambda name: f"value-for-{name}",
                ),
                patch(
                    "src.generate_portrait.analyze_transcript.positive_integer_setting",
                    side_effect=lambda _name, default: default,
                ),
                patch(
                    "src.generate_portrait.export_markdown.export_markdown",
                    side_effect=export_markdown,
                ),
                patch(
                    "src.generate_portrait.analyze_transcript.analyze_all_members",
                    return_value="# 群员画像分析",
                ),
                patch(
                    "src.generate_portrait.analyze_transcript.render_html",
                    return_value="<html></html>",
                ),
                patch(
                    "src.generate_portrait.analyze_transcript.resolve_output_path",
                    side_effect=lambda output_dir: output_dir / "20260911083045_群员画像.html",
                ),
                patch(
                    "src.generate_portrait.render_html_png.render_html_to_pngs",
                    side_effect=render_html_to_pngs,
                ),
                patch.dict(
                    os.environ,
                    {"PNG_DPI": "144", "PNG_MAX_HEIGHT_PIXELS": "12000"},
                    clear=False,
                ),
            ):
                paths = generate_portrait.generate_portrait(
                    generate_portrait.export_markdown.parse_date("2026-09-11"),
                    generate_portrait.export_markdown.parse_date("2026-09-11"),
                    output_dir,
                    timezone=ZoneInfo("Asia/Shanghai"),
                    env_file=Path(".env"),
                    top_members=None,
                    min_message_count=0,
                    dpi=None,
                    width_millimeters=210.0,
                    max_height_pixels=None,
                    stitch_horizontal=True,
                    stitch_count=4,
                )

            self.assertEqual(paths, [captured["output_path"]])
            self.assertTrue(output_dir.is_dir())
            self.assertEqual(
                captured["render_options"],
                {
                    "dpi": 144,
                    "width_millimeters": 210.0,
                    "max_height_pixels": 12000,
                    "stitch_horizontal": True,
                    "stitch_count": 4,
                },
            )
            markdown_path = captured["markdown_path"]
            html_path = captured["html_path"]
            assert isinstance(markdown_path, Path)
            assert isinstance(html_path, Path)
            self.assertFalse(markdown_path.exists())
            self.assertTrue(html_path.is_file())
            self.assertEqual(html_path.parent, output_dir)
