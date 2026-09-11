"""Tests for PNG rendering command-line configuration."""

import os
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from src import render_html_png


class ArgumentParserTests(unittest.TestCase):
    def test_uses_environment_dpi_when_option_is_omitted(self) -> None:
        """The configured DPI must become the CLI default."""

        with patch.dict(os.environ, {"PNG_DPI": "144"}, clear=False):
            args = render_html_png.build_argument_parser().parse_args(
                ["input.html", "output"]
            )

        self.assertEqual(args.dpi, 144)

    def test_explicit_dpi_overrides_environment_default(self) -> None:
        """A command-line DPI must take precedence over configuration."""

        with patch.dict(os.environ, {"PNG_DPI": "144"}, clear=False):
            args = render_html_png.build_argument_parser().parse_args(
                ["input.html", "output", "--dpi", "200"]
            )

        self.assertEqual(args.dpi, 200)

    def test_uses_environment_height_limit_when_option_is_omitted(self) -> None:
        """The configured image height must become the CLI default."""

        with patch.dict(os.environ, {"PNG_MAX_HEIGHT_PIXELS": "12000"}, clear=False):
            args = render_html_png.build_argument_parser().parse_args(
                ["input.html", "output"]
            )

        self.assertEqual(args.max_height_px, 12000)

    def test_explicit_height_limit_overrides_environment_default(self) -> None:
        """A command-line height limit must take precedence over configuration."""

        with patch.dict(os.environ, {"PNG_MAX_HEIGHT_PIXELS": "12000"}, clear=False):
            args = render_html_png.build_argument_parser().parse_args(
                ["input.html", "output", "--max-height-px", "8000"]
            )

        self.assertEqual(args.max_height_px, 8000)

    def test_horizontal_stitch_defaults_to_four_segments(self) -> None:
        """Horizontal stitching is opt-in and groups four segments by default."""

        args = render_html_png.build_argument_parser().parse_args(
            ["input.html", "output", "--stitch-horizontal"]
        )

        self.assertTrue(args.stitch_horizontal)
        self.assertEqual(args.stitch_count, 4)

    def test_horizontal_stitch_accepts_a_custom_segment_count(self) -> None:
        """The stitch count controls how many segments form each output image."""

        args = render_html_png.build_argument_parser().parse_args(
            [
                "input.html",
                "output",
                "--stitch-horizontal",
                "--stitch-count",
                "2",
            ]
        )

        self.assertTrue(args.stitch_horizontal)
        self.assertEqual(args.stitch_count, 2)

    def test_creates_directory_and_uses_timestamped_filename(self) -> None:
        """The second positional argument is an output directory."""

        with TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "portraits"
            output_path = render_html_png.create_output_path(
                output_dir,
                generated_at=datetime(2026, 9, 11, 8, 30, 45),
            )

            self.assertTrue(output_dir.is_dir())
            self.assertEqual(output_path.name, "20260911083045_群员画像.png")

    def test_numbers_split_output_paths(self) -> None:
        """Split renders append one-based sequence suffixes to the base filename."""

        output_path = Path("output") / "20260911083045_群员画像.png"

        self.assertEqual(
            render_html_png.output_paths(output_path, 3),
            [
                Path("output") / "20260911083045_群员画像_1.png",
                Path("output") / "20260911083045_群员画像_2.png",
                Path("output") / "20260911083045_群员画像_3.png",
            ],
        )

    def test_stitching_uses_one_output_path_per_group(self) -> None:
        """Four vertical segments need one final output when the group size is four."""

        output_path = Path("output") / "20260911083045_群员画像.png"

        self.assertEqual(
            render_html_png.output_paths(output_path, 1),
            [Path("output") / "20260911083045_群员画像.png"],
        )


if __name__ == "__main__":
    unittest.main()
