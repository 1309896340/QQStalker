"""File-backed cache for successful LLM responses keyed by request identity."""

import hashlib
import json
import os
from pathlib import Path

_CACHE_DIRNAME = ".llm-cache"
_ACTIVE_CACHE: "LlmResponseCache | None" = None


def cache_key(
    *,
    stage_label: str | None,
    base_url: str,
    model: str,
    max_tokens: int | None,
    prompt: str,
) -> str:
    """Hash everything that shapes the response text into one stable key."""

    material = json.dumps(
        {
            "stage": stage_label or "",
            "base_url": base_url,
            "model": model,
            "max_tokens": max_tokens,
            "prompt": prompt,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class LlmResponseCache:
    """Stores successful responses as one JSON file per request key."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory / _CACHE_DIRNAME

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def lookup(self, key: str) -> tuple[str, str | None] | None:
        """Return the cached response pair, dropping unreadable entries."""

        path = self._path(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
            self._discard(path)
            return None
        if not isinstance(data, dict):
            self._discard(path)
            return None
        response = data.get("response")
        finish_reason = data.get("finish_reason")
        if not isinstance(response, str) or not response:
            self._discard(path)
            return None
        if finish_reason is not None and not isinstance(finish_reason, str):
            self._discard(path)
            return None
        return response, finish_reason

    @staticmethod
    def _discard(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            return

    def store(self, key: str, response: str, finish_reason: str | None) -> None:
        """Persist one response pair atomically; failures are silently skipped."""

        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self._path(key)
            tmp_path = path.with_name(path.name + ".tmp")
            tmp_path.write_text(
                json.dumps(
                    {"response": response, "finish_reason": finish_reason},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(tmp_path, path)
        except OSError:
            return


def install(cache: LlmResponseCache) -> None:
    """Make the cache active for every request in this process."""

    global _ACTIVE_CACHE
    _ACTIVE_CACHE = cache


def uninstall() -> None:
    """Drop the process-level cache (used by tests and --no-cache paths)."""

    global _ACTIVE_CACHE
    _ACTIVE_CACHE = None


def active() -> "LlmResponseCache | None":
    return _ACTIVE_CACHE
