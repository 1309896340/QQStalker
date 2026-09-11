"""Tests for PNG rendering command-line configuration."""

import os
import unittest
from unittest.mock import patch

from src import render_html_png


class ArgumentParserTests(unittest.TestCase):
    def test_uses_environment_dpi_when_option_is_omitted(self) -> None:
        """The configured DPI must become the CLI default."""

        with patch.dict(os.environ, {"PNG_DPI": "144"}, clear=False):
            args = render_html_png.build_argument_parser().parse_args(
                ["input.html", "output.png"]
            )

        self.assertEqual(args.dpi, 144)

    def test_explicit_dpi_overrides_environment_default(self) -> None:
        """A command-line DPI must take precedence over configuration."""

        with patch.dict(os.environ, {"PNG_DPI": "144"}, clear=False):
            args = render_html_png.build_argument_parser().parse_args(
                ["input.html", "output.png", "--dpi", "200"]
            )

        self.assertEqual(args.dpi, 200)

    def test_uses_environment_height_limit_when_option_is_omitted(self) -> None:
        """The configured image height must become the CLI default."""

        with patch.dict(os.environ, {"PNG_MAX_HEIGHT_PIXELS": "12000"}, clear=False):
            args = render_html_png.build_argument_parser().parse_args(
                ["input.html", "output.png"]
            )

        self.assertEqual(args.max_height_px, 12000)

    def test_explicit_height_limit_overrides_environment_default(self) -> None:
        """A command-line height limit must take precedence over configuration."""

        with patch.dict(os.environ, {"PNG_MAX_HEIGHT_PIXELS": "12000"}, clear=False):
            args = render_html_png.build_argument_parser().parse_args(
                ["input.html", "output.png", "--max-height-px", "8000"]
            )

        self.assertEqual(args.max_height_px, 8000)


if __name__ == "__main__":
    unittest.main()
