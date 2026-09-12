"""FastAPI operational surface and NapCat forward WebSocket synchronizer."""

from __future__ import annotations

import asyncio
import json
import random
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlmodel import SQLModel, Session
import websockets

from src.qqstalker_core.database import create_database_engine
from src.qqstalker_core.persistence import mark_group_message_recalled, persist_group_message
from src.qqstalker_realtime.config import Settings, load_settings
from src.qqstalker_realtime.onebot import GroupMessage, GroupRecall

SyncOutcome = Literal["synced", "duplicate", "recalled", "ignored"]


def _now() -> datetime:
    return datetime.now(UTC)


def _increment(app: FastAPI, name: str) -> None:
    app.state.counts[name] += 1


def _set_connection_state(app: FastAPI, *, connected: bool, error: Exception | None = None) -> None:
    """Update state without retaining exception text, event contents, or credentials."""

    app.state.connected = connected
    app.state.last_connection_change = _now()
    if connected:
        app.state.first_connected = True
        app.state.last_error = None
    elif error is not None:
        app.state.last_error = type(error).__name__


def reconnect_delay(settings: Settings, attempt: int, *, random_value: float | None = None) -> float:
    """Return a bounded exponential delay with ±20% jitter after a disconnect."""

    base_delay = min(
        settings.reconnect_initial_seconds * (2**attempt),
        settings.reconnect_max_seconds,
    )
    jitter_source = random_value if random_value is not None else random.uniform(-0.2, 0.2)
    return min(
        settings.reconnect_max_seconds,
        max(0.0, base_delay * (1.0 + jitter_source)),
    )


async def _open_connection(settings: Settings) -> Any:
    """Open the authenticated, forward WebSocket connection to NapCat."""

    return await websockets.connect(
        settings.napcat_url,
        additional_headers={"Authorization": f"Bearer {settings.napcat_token}"},
        open_timeout=settings.connect_timeout_seconds,
    )


def _preflight_database(engine: Engine) -> None:
    """Check the database and create the shared schema before accepting events."""

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    SQLModel.metadata.create_all(engine)


def dispatch_onebot_event(engine: Engine, settings: Settings, raw_event: str | bytes) -> SyncOutcome:
    """Validate, whitelist, and persist one event in its own short transaction."""

    try:
        payload = json.loads(raw_event)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return "ignored"
    if not isinstance(payload, dict):
        return "ignored"

    try:
        if payload.get("post_type") == "message" and payload.get("message_type") == "group":
            message = GroupMessage.model_validate(payload)
            if str(message.group_id) not in settings.allowed_group_ids:
                return "ignored"
            with Session(engine) as session, session.begin():
                return "synced" if persist_group_message(session, message) else "duplicate"

        if payload.get("post_type") == "notice" and payload.get("notice_type") == "group_recall":
            recall = GroupRecall.model_validate(payload)
            if str(recall.group_id) not in settings.allowed_group_ids:
                return "ignored"
            with Session(engine) as session, session.begin():
                return (
                    "recalled"
                    if mark_group_message_recalled(session, recall.group_id, recall.message_id)
                    else "ignored"
                )
    except (ValidationError, ValueError, TypeError):
        return "ignored"
    return "ignored"


async def _consume_connection(app: FastAPI, settings: Settings, socket: Any) -> None:
    """Consume one open connection without logging payloads or remote resource URLs."""

    async for raw_event in socket:
        _increment(app, "received")
        try:
            outcome = dispatch_onebot_event(app.state.engine, settings, raw_event)
        except Exception as error:
            _increment(app, "failed")
            app.state.last_error = type(error).__name__
            continue
        _increment(app, outcome)
        if outcome in {"synced", "recalled"}:
            app.state.last_success_at = _now()


async def _close_socket(socket: Any) -> None:
    """Close a socket when it supplies the standard WebSocket close method."""

    if socket is not None:
        with suppress(Exception):
            await socket.close()


async def _consume(app: FastAPI, settings: Settings, initial_socket: Any) -> None:
    """Maintain a post-startup connection with bounded exponential backoff."""

    socket = initial_socket
    reconnect_attempt = 0
    while True:
        try:
            if socket is None:
                socket = await _open_connection(settings)
                reconnect_attempt = 0
                _set_connection_state(app, connected=True)
            await _consume_connection(app, settings, socket)
            raise ConnectionError("NapCat WebSocket 已关闭")
        except asyncio.CancelledError:
            await _close_socket(socket)
            raise
        except Exception as error:
            await _close_socket(socket)
            socket = None
            _set_connection_state(app, connected=False, error=error)
            delay = reconnect_delay(settings, reconnect_attempt)
            reconnect_attempt += 1
            await asyncio.sleep(delay)


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    """Create the local-only management API and its lifespan-managed worker."""

    resolved_settings = settings or load_settings()
    resolved_engine = engine or create_database_engine()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            _preflight_database(resolved_engine)
        except Exception:
            app.state.database_available = False
            raise RuntimeError("PostgreSQL 数据库连接或初始化不可达") from None
        app.state.database_available = True

        try:
            initial_socket = await _open_connection(resolved_settings)
        except Exception:
            raise RuntimeError(
                "目标 NapCat WebSocket Server 连接不可达："
                f"{resolved_settings.napcat_endpoint_description}"
            ) from None

        _set_connection_state(app, connected=True)
        app.state.worker = asyncio.create_task(_consume(app, resolved_settings, initial_socket))
        try:
            yield
        finally:
            app.state.worker.cancel()
            await asyncio.gather(app.state.worker, return_exceptions=True)

    app = FastAPI(title="QQStalker realtime sync", lifespan=lifespan)
    app.state.engine = resolved_engine
    app.state.connected = False
    app.state.first_connected = False
    app.state.database_available = False
    app.state.last_connection_change = None
    app.state.last_success_at = None
    app.state.last_error = None
    app.state.counts = {
        "received": 0,
        "synced": 0,
        "duplicate": 0,
        "recalled": 0,
        "ignored": 0,
        "failed": 0,
    }

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        if not app.state.database_available or not app.state.connected:
            raise HTTPException(status_code=503, detail="同步服务尚未就绪")
        return {"status": "ready"}

    @app.get("/sync/status")
    def sync_status() -> dict[str, object]:
        return {
            "connected": app.state.connected,
            "first_connected": app.state.first_connected,
            "database_available": app.state.database_available,
            "last_connection_change": (
                app.state.last_connection_change.isoformat()
                if app.state.last_connection_change is not None
                else None
            ),
            "last_success_at": (
                app.state.last_success_at.isoformat()
                if app.state.last_success_at is not None
                else None
            ),
            "counts": dict(app.state.counts),
            "last_error": app.state.last_error,
        }

    return app
