"""Configuration for the local NapCat synchronizer."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlunsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def load_project_dotenv(env_path: Path = Path(".env")) -> None:
    """Load simple project settings without overwriting explicit environment values."""

    if not env_path.is_file():
        return
    for line_number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"{env_path}:{line_number} 不是 KEY=VALUE 格式")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise RuntimeError(f"{env_path}:{line_number} 的变量名不能为空")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _integer_setting(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read a bounded integer while keeping sensitive values out of errors."""

    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} 必须是整数") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} 必须介于 {minimum} 和 {maximum} 之间")
    return value


def _float_setting(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """Read a bounded float while keeping sensitive values out of errors."""

    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} 必须是数字") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} 必须介于 {minimum} 和 {maximum} 之间")
    return value


@dataclass(frozen=True)
class Settings:
    """All real-time settings, with a representation that hides its token."""

    napcat_host: str
    napcat_port: int
    napcat_path: str
    napcat_token: str = field(repr=False)
    allowed_group_ids: frozenset[str] = field(default_factory=frozenset)
    napcat_scheme: str = "ws"
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    connect_timeout_seconds: float = 10.0
    api_host: str = "127.0.0.1"
    api_port: int = 8010

    @property
    def napcat_url(self) -> str:
        """Return the authenticated endpoint without ever embedding the token."""

        return urlunsplit(
            (
                self.napcat_scheme,
                f"{self.napcat_host}:{self.napcat_port}",
                self.napcat_path,
                "",
                "",
            )
        )

    @property
    def napcat_endpoint_description(self) -> str:
        """Return the non-sensitive parameters safe to include in startup errors."""

        return (
            f"scheme={self.napcat_scheme} host={self.napcat_host} "
            f"port={self.napcat_port} path={self.napcat_path}"
        )


def load_settings() -> Settings:
    """Load settings without ever including the token in exception text."""

    load_project_dotenv()
    token = os.getenv("NAPCAT_WS_TOKEN", "")
    if not token:
        raise RuntimeError("NAPCAT_WS_TOKEN 未配置")

    scheme = os.getenv("NAPCAT_WS_SCHEME", "ws").lower()
    if scheme not in {"ws", "wss"}:
        raise RuntimeError("NAPCAT_WS_SCHEME 必须是 ws 或 wss")
    path = os.getenv("NAPCAT_WS_PATH", "/")
    if not path.startswith("/"):
        raise RuntimeError("NAPCAT_WS_PATH 必须以 / 开头")
    api_host = os.getenv("NAPCAT_API_HOST", "127.0.0.1")
    if api_host not in LOOPBACK_HOSTS:
        raise RuntimeError("NAPCAT_API_HOST 必须是回环地址")

    groups = frozenset(
        item.strip()
        for item in os.getenv("NAPCAT_ALLOWED_GROUP_IDS", "").split(",")
        if item.strip()
    )
    reconnect_initial = _float_setting(
        "NAPCAT_RECONNECT_INITIAL_SECONDS",
        1.0,
        minimum=0.1,
        maximum=30.0,
    )
    reconnect_max = _float_setting(
        "NAPCAT_RECONNECT_MAX_SECONDS",
        30.0,
        minimum=reconnect_initial,
        maximum=300.0,
    )
    return Settings(
        napcat_host=os.getenv("NAPCAT_WS_HOST", "127.0.0.1"),
        napcat_port=_integer_setting("NAPCAT_WS_PORT", 3001, minimum=1, maximum=65535),
        napcat_path=path,
        napcat_token=token,
        allowed_group_ids=groups,
        napcat_scheme=scheme,
        reconnect_initial_seconds=reconnect_initial,
        reconnect_max_seconds=reconnect_max,
        connect_timeout_seconds=_float_setting(
            "NAPCAT_CONNECT_TIMEOUT_SECONDS",
            10.0,
            minimum=0.1,
            maximum=300.0,
        ),
        api_host=api_host,
        api_port=_integer_setting("NAPCAT_API_PORT", 8010, minimum=1, maximum=65535),
    )
