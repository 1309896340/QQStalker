"""Shared progress presentation for concurrent LLM requests.

并发大模型请求共用一个多行进度区：每个进行中的请求各占一行、独立刷新，
结果性输出经受控通道打印在进度区上方；stdout 重定向时退化为仅在请求
开始与完成时逐行输出。
"""

from __future__ import annotations

import sys
import threading
from types import TracebackType
from typing import Any, Protocol, Self

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

PROGRESS_REFRESH_SECONDS = 0.1


class LlmProgressReporter(Protocol):
    """Report per-request progress rows and stage messages."""

    def begin(self, label: str) -> object:
        """Register one in-flight request row and return its token."""
        ...

    def update(
        self,
        token: object,
        *,
        received_chars: int = 0,
        note: str | None = None,
    ) -> None:
        """Refresh one request row with received characters and a status note."""
        ...

    def finish(self, token: object, *, ok: bool = True) -> None:
        """Settle one request row after it completes or fails."""
        ...

    def print(self, text: str) -> None:
        """Print a stage message above the progress area without breaking rows."""
        ...

    def close(self) -> None:
        """Stop the underlying rendering so later output starts on fresh lines."""
        ...

    def __enter__(self) -> Self:
        """Start rendering; usable as a context manager."""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop rendering on exit."""
        ...


class LineProgressReporter:
    """Redirected-output fallback: start/finish lines only, no live rows."""

    def begin(self, label: str) -> object:
        return label

    def update(
        self,
        token: object,
        *,
        received_chars: int = 0,
        note: str | None = None,
    ) -> None:
        return None

    def finish(self, token: object, *, ok: bool = True) -> None:
        return None

    def print(self, text: str) -> None:
        print(text, flush=True)

    def close(self) -> None:
        return None

    def __enter__(self) -> "LineProgressReporter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class RichProgressReporter:
    """One rich Progress row per in-flight request, updated concurrently."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TextColumn("[dim]{task.fields[note]}"),
            TimeElapsedColumn(),
            TextColumn("[progress.percentage]已接收 {task.fields[received]} 字"),
            console=self._console,
            refresh_per_second=1 / PROGRESS_REFRESH_SECONDS,
        )
        self._started = False
        self._start_lock = threading.Lock()

    def begin(self, label: str) -> object:
        self._ensure_started()
        return self._progress.add_task(label, note="", received=0)

    def update(
        self,
        token: object,
        *,
        received_chars: int = 0,
        note: str | None = None,
    ) -> None:
        if not isinstance(token, int):
            return
        fields: dict[str, Any] = {}
        if received_chars:
            fields["received"] = received_chars
        if note is not None:
            fields["note"] = note
        self._progress.update(TaskID(token), **fields)

    def finish(self, token: object, *, ok: bool = True) -> None:
        if not isinstance(token, int):
            return
        if ok:
            self._progress.remove_task(TaskID(token))
            return
        self._progress.update(TaskID(token), note="[red]失败[/red]")
        self._progress.stop_task(TaskID(token))

    def print(self, text: str) -> None:
        self._console.print(text, markup=False, highlight=False)

    def close(self) -> None:
        if self._started:
            self._progress.stop()
            self._started = False

    def _ensure_started(self) -> None:
        with self._start_lock:
            if not self._started:
                self._progress.start()
                self._started = True

    def __enter__(self) -> "RichProgressReporter":
        self._ensure_started()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def stdout_is_interactive() -> bool:
    """Whether the current stdout can host a live progress area."""

    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def create_llm_progress_reporter(console: Console | None = None) -> LlmProgressReporter:
    """Pick the live progress area on terminals, line output when redirected."""

    if console is not None:
        return RichProgressReporter(console=console)
    if not stdout_is_interactive():
        return LineProgressReporter()
    return RichProgressReporter()
