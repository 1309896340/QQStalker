"""Bounded, order-preserving concurrency for independent LLM requests."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")
U = TypeVar("U")


def run_items(
    items: tuple[T, ...],
    *,
    worker: Callable[[T], U],
    maximum_workers: int,
) -> tuple[U, ...]:
    """Run independent per-item tasks concurrently, preserving input order."""

    if maximum_workers <= 1 or len(items) <= 1:
        return tuple(worker(item) for item in items)
    with ThreadPoolExecutor(max_workers=min(maximum_workers, len(items))) as executor:
        return tuple(executor.map(worker, items))
