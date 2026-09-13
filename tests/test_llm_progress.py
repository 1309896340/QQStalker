"""Tests for the shared concurrent LLM progress presentation."""

import io
import threading
import time
import unittest
from io import StringIO
from unittest.mock import patch

from rich.console import Console

from src.qqstalker_cli import llm_progress
from src.qqstalker_cli.llm_progress import (
    LineProgressReporter,
    RichProgressReporter,
    create_llm_progress_reporter,
)


def make_terminal_console(buffer: StringIO | None = None) -> Console:
    return Console(
        file=buffer or StringIO(),
        force_terminal=True,
        width=120,
        legacy_windows=False,
    )


class RichProgressReporterTests(unittest.TestCase):
    def test_concurrent_rows_render_simultaneously(self) -> None:
        """Two in-flight requests must occupy two rows at the same time."""

        console = make_terminal_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            first = reporter.begin("讨论纪要（第 1 次请求）")
            second = reporter.begin("讨论纪要（第 2 次请求）")
            reporter.update(first, received_chars=120)
            reporter.update(second, received_chars=80)
            time.sleep(0.3)

        rendered = console.file.getvalue()  # type: ignore[attr-defined]
        self.assertIn("讨论纪要（第 1 次请求）", rendered)
        self.assertIn("讨论纪要（第 2 次请求）", rendered)
        self.assertIn("已接收 120 字", rendered)
        self.assertIn("已接收 80 字", rendered)

    def test_successful_finish_removes_the_row(self) -> None:
        """A completed request must free its row while the others keep running."""

        console = make_terminal_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            first = reporter.begin("任务甲")
            second = reporter.begin("任务乙")
            reporter.finish(first, ok=True)
            self.assertEqual(reporter._progress.task_ids, [second])

    def test_failed_finish_keeps_a_frozen_row(self) -> None:
        """A failed request must leave a settled failure row until close."""

        console = make_terminal_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            token = reporter.begin("讨论纪要（第 3 次请求）")
            reporter.finish(token, ok=False)
            self.assertEqual(reporter._progress.task_ids, [token])
            task = reporter._progress.tasks[0]
            self.assertIn("失败", task.fields["note"])
            self.assertIsNotNone(task.stop_time)

    def test_non_terminal_console_prints_without_ansi(self) -> None:
        """A non-terminal console must only receive plain stage messages."""

        output = StringIO()
        console = Console(file=output, force_terminal=False, width=120)
        reporter = RichProgressReporter(console=console)
        with reporter:
            token = reporter.begin("任务甲")
            reporter.update(token, received_chars=50)
            reporter.finish(token, ok=True)
            reporter.print("任务甲：大模型响应完成")

        self.assertNotIn("\x1b[", output.getvalue())
        self.assertIn("任务甲：大模型响应完成", output.getvalue())

    def test_concurrent_updates_from_threads_do_not_break_rows(self) -> None:
        """Concurrent updates must all land without corrupting the area."""

        console = make_terminal_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            tokens = [reporter.begin(f"并发任务 {index}") for index in range(4)]

            def update_own(token: object) -> None:
                for received in (10, 20, 30):
                    reporter.update(token, received_chars=received)

            threads = [
                threading.Thread(target=update_own, args=(token,)) for token in tokens
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            for index, token in enumerate(tokens):
                task = reporter._progress.tasks[index]
                self.assertEqual(task.fields["received"], 30)


class LineProgressReporterTests(unittest.TestCase):
    def test_print_writes_plain_lines_and_lifecycle_is_silent(self) -> None:
        """The fallback must only emit explicit stage messages."""

        output = StringIO()
        with (
            patch("sys.stdout", output),
            LineProgressReporter() as reporter,
        ):
            token = reporter.begin("任务甲")
            reporter.update(token, received_chars=10)
            reporter.finish(token, ok=True)
            reporter.print("任务甲：大模型响应完成")

        self.assertEqual(output.getvalue(), "任务甲：大模型响应完成\n")


class InteractiveStdout(StringIO):
    """A stdout double that claims to be an interactive terminal."""

    def isatty(self) -> bool:
        return True


class FactoryTests(unittest.TestCase):
    def test_redirect_output_selects_the_line_reporter(self) -> None:
        with patch.object(llm_progress.sys, "stdout", io.StringIO()):
            reporter = create_llm_progress_reporter()
        self.assertIsInstance(reporter, LineProgressReporter)

    def test_interactive_output_selects_the_rich_reporter(self) -> None:
        with patch.object(llm_progress.sys, "stdout", InteractiveStdout()):
            reporter = create_llm_progress_reporter()
        self.assertIsInstance(reporter, RichProgressReporter)
        reporter.close()


if __name__ == "__main__":
    unittest.main()
