"""Tests for the standalone streaming debug entry."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.qqstalker_cli import analyze_transcript, stream_debug

TRANSCRIPT = """## 2026-09-11 19:00:00 · 测试群

> **甲**
>
> 消息一

## 2026-09-11 20:00:00 · 测试群

> **乙**
>
> 消息二
"""

ENV_TEXT = "LLM_BASE_URL=https://example.test\nLLM_MODEL=test-model\nLLM_API_KEY=test-key\n"


class StreamDebugTests(unittest.TestCase):
    def test_builds_first_batch_request_and_reports_summary(self) -> None:
        """The debug entry must issue one labeled batch request and summarize it."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sample = root / "样本.md"
            sample.write_text(TRANSCRIPT, encoding="utf-8")
            env_file = root / ".env"
            env_file.write_text(ENV_TEXT, encoding="utf-8")
            args = stream_debug.build_argument_parser().parse_args(
                ["--input", str(sample), "--env-file", str(env_file)]
            )
            with patch.object(
                analyze_transcript,
                "request_portraits",
                return_value=("### 甲\n- 概括", "stop"),
            ) as request_mock:
                output = StringIO()
                with redirect_stdout(output):
                    content, finish_reason, _, retries = stream_debug.run_debug(args)

        request_kwargs = request_mock.call_args.kwargs
        self.assertEqual(request_kwargs["stage_label"], "流式调试")
        self.assertEqual(request_kwargs["attempt_counter"], [])
        self.assertEqual(content, "### 甲\n- 概括")
        self.assertEqual(finish_reason, "stop")
        self.assertEqual(retries, 0)
        self.assertIn("流式调试：使用样本", output.getvalue())
        self.assertIn("流式调试完成", output.getvalue())
        self.assertIn("内容预览", output.getvalue())

    def test_missing_sample_fails_before_any_request(self) -> None:
        """A missing transcript must abort before contacting the LLM."""

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file = root / ".env"
            env_file.write_text(ENV_TEXT, encoding="utf-8")
            args = stream_debug.build_argument_parser().parse_args(
                ["--input", str(root / "不存在.md"), "--env-file", str(env_file)]
            )
            with patch.object(analyze_transcript, "request_portraits") as request_mock:
                with self.assertRaisesRegex(FileNotFoundError, "消息记录文件不存在"):
                    stream_debug.run_debug(args)

        request_mock.assert_not_called()

    def test_defaults_target_the_local_sample_and_env(self) -> None:
        """The default input is the fixed local debug sample."""

        args = stream_debug.build_argument_parser().parse_args([])

        self.assertEqual(args.input, Path("exports/20260911194123_消息记录.md"))
        self.assertEqual(args.env_file, Path(".env"))


if __name__ == "__main__":
    unittest.main()
