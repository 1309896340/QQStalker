"""Tests for the NapCat forward WebSocket synchronizer."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.routing import APIRoute
from sqlmodel import SQLModel, Session, create_engine, select

from src.qqstalker_core.models import (
    BinaryResource,
    Chat,
    Message,
    MessageElement,
    MessageMention,
    MessageResource,
)
from src.qqstalker_core.persistence import persist_group_message
from src.qqstalker_realtime.app import (
    create_app,
    dispatch_onebot_event,
    reconnect_delay,
)
from src.qqstalker_realtime.config import Settings, load_project_dotenv, load_settings
from src.qqstalker_realtime.onebot import GroupMessage


class EventSocket:
    """A small in-process WebSocket double that yields scripted event payloads."""

    def __init__(self, events: list[str], *, block_after_events: bool = False) -> None:
        self.events = events
        self.block_after_events = block_after_events
        self.closed = False
        self._released = asyncio.Event()

    def __aiter__(self) -> AsyncIterator[str]:
        return self._events()

    async def _events(self) -> AsyncIterator[str]:
        for event in self.events:
            yield event
        if self.block_after_events:
            await self._released.wait()

    async def close(self) -> None:
        self.closed = True
        self._released.set()


def settings(*, groups: frozenset[str] = frozenset({"10001"})) -> Settings:
    """Build safe test-only settings without putting credentials in environment."""

    return Settings(
        napcat_host="127.0.0.1",
        napcat_port=3001,
        napcat_path="/",
        napcat_token="test-token-not-for-production",
        allowed_group_ids=groups,
        reconnect_initial_seconds=0.1,
        reconnect_max_seconds=0.1,
    )


def group_message(*, message_id: int = 9001, group_id: int = 10001) -> str:
    """Return one sanitized array-format ordinary group-message fixture."""

    return json.dumps(
        {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "group_id": group_id,
            "message_id": message_id,
            "user_id": 20002,
            "time": 1_700_000_000,
            "message": [
                {"type": "text", "data": {"text": "测试文本"}},
                {"type": "at", "data": {"qq": "30003", "name": "被提及成员"}},
                {
                    "type": "image",
                    "data": {
                        "file": "test-image.png",
                        "url": "https://example.invalid/test-image.png",
                    },
                },
            ],
            "raw_message": "测试文本@被提及成员[图片]",
            "sender": {"nickname": "昵称", "card": "群名片"},
        },
        ensure_ascii=False,
    )


def group_recall(*, message_id: int, group_id: int = 10001) -> str:
    """Return one sanitized OneBot group-recall notice fixture."""

    return json.dumps(
        {
            "post_type": "notice",
            "notice_type": "group_recall",
            "group_id": group_id,
            "message_id": message_id,
            "time": 1_700_000_001,
        }
    )


class ConfigurationTests(unittest.TestCase):
    def test_project_dotenv_preserves_explicit_environment_values(self) -> None:
        """The service reads local configuration without replacing process-supplied secrets."""

        with TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text("NAPCAT_WS_TOKEN=from-file\nNAPCAT_WS_PORT=3001\n", encoding="utf-8")
            with patch.dict(os.environ, {"NAPCAT_WS_TOKEN": "from-process"}, clear=True):
                load_project_dotenv(env_path)
                self.assertEqual(os.environ["NAPCAT_WS_TOKEN"], "from-process")
                self.assertEqual(os.environ["NAPCAT_WS_PORT"], "3001")

    def test_loads_safe_settings_and_hides_token_from_representation_and_errors(self) -> None:
        """Configuration accepts all operational controls without exposing its token."""

        token = "real-test-token-value"
        with patch.dict(
            os.environ,
            {
                "NAPCAT_WS_TOKEN": token,
                "NAPCAT_WS_HOST": "127.0.0.1",
                "NAPCAT_WS_PORT": "3001",
                "NAPCAT_WS_PATH": "/onebot/v11",
                "NAPCAT_ALLOWED_GROUP_IDS": "10001, 10002",
                "NAPCAT_RECONNECT_INITIAL_SECONDS": "1",
                "NAPCAT_RECONNECT_MAX_SECONDS": "30",
                "NAPCAT_API_HOST": "127.0.0.1",
                "NAPCAT_API_PORT": "8010",
            },
            clear=True,
        ):
            loaded = load_settings()

        self.assertEqual(loaded.napcat_url, "ws://127.0.0.1:3001/onebot/v11")
        self.assertEqual(loaded.allowed_group_ids, frozenset({"10001", "10002"}))
        self.assertNotIn(token, repr(loaded))
        self.assertNotIn(token, loaded.napcat_endpoint_description)

    def test_rejects_non_loopback_operational_api(self) -> None:
        """The management API can never accidentally bind to an external address."""

        with patch.dict(
            os.environ,
            {"NAPCAT_WS_TOKEN": "safe", "NAPCAT_API_HOST": "0.0.0.0"},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "回环地址"):
                load_settings()


class OneBotModelTests(unittest.TestCase):
    def test_parses_array_segments_and_rejects_non_normal_group_messages(self) -> None:
        """Only ordinary array-format group messages are part of this synchronizer."""

        valid = GroupMessage.model_validate_json(group_message())
        self.assertEqual(valid.message[0].data["text"], "测试文本")
        malformed = json.loads(group_message())
        malformed["sub_type"] = "anonymous"
        with self.assertRaises(ValueError):
            GroupMessage.model_validate(malformed)


class PersistenceAndDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)

    def messages(self) -> list[Message]:
        with Session(self.engine) as session:
            return list(session.exec(select(Message)).all())

    def test_whitelist_filters_non_group_non_normal_and_malformed_events_without_writes(self) -> None:
        """Ignored inputs never start a persistence transaction or create a chat."""

        self.assertEqual(dispatch_onebot_event(self.engine, settings(groups=frozenset()), group_message()), "ignored")
        private_message = json.loads(group_message())
        private_message["message_type"] = "private"
        self.assertEqual(
            dispatch_onebot_event(self.engine, settings(), json.dumps(private_message)),
            "ignored",
        )
        other_group = group_message(group_id=10002)
        self.assertEqual(dispatch_onebot_event(self.engine, settings(), other_group), "ignored")
        self.assertEqual(dispatch_onebot_event(self.engine, settings(), "not-json"), "ignored")
        non_normal = json.loads(group_message())
        non_normal["sub_type"] = "anonymous"
        self.assertEqual(
            dispatch_onebot_event(self.engine, settings(), json.dumps(non_normal)),
            "ignored",
        )
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(Chat)).all()), 0)
            self.assertEqual(len(session.exec(select(Message)).all()), 0)

    def test_persists_structured_message_without_downloading_remote_attachment(self) -> None:
        """One allowed message creates shared entities, elements, mention, and metadata only."""

        self.assertEqual(dispatch_onebot_event(self.engine, settings(), group_message()), "synced")
        with Session(self.engine) as session:
            message = session.exec(select(Message)).one()
            self.assertEqual(message.text, "测试文本")
            self.assertEqual(message.import_batch_id, None)
            self.assertEqual(len(session.exec(select(MessageElement)).all()), 3)
            mention = session.exec(select(MessageMention)).one()
            self.assertEqual(mention.mentioned_uid, "30003")
            resource = session.exec(select(MessageResource)).one()
            self.assertEqual(resource.resource_name, "test-image.png")
            self.assertEqual(resource.binary_resource_id, None)
            self.assertEqual(len(session.exec(select(BinaryResource)).all()), 0)
            self.assertEqual(message.raw_content_json["resources"][0]["type"], "image")

    def test_replay_is_idempotent_and_recall_preserves_known_content(self) -> None:
        """A replay creates no duplicate rows; known recalls only flip the retained message flag."""

        original = group_message(message_id=9001)
        self.assertEqual(dispatch_onebot_event(self.engine, settings(), original), "synced")
        self.assertEqual(dispatch_onebot_event(self.engine, settings(), original), "duplicate")
        self.assertEqual(dispatch_onebot_event(self.engine, settings(), group_recall(message_id=9001)), "recalled")
        self.assertEqual(dispatch_onebot_event(self.engine, settings(), group_recall(message_id=9999)), "ignored")
        with Session(self.engine) as session:
            messages = list(session.exec(select(Message)).all())
            self.assertEqual(len(messages), 1)
            self.assertTrue(messages[0].recalled)
            self.assertEqual(messages[0].text, "测试文本")
            self.assertEqual(len(session.exec(select(MessageElement)).all()), 3)
            self.assertEqual(len(session.exec(select(MessageMention)).all()), 1)
            self.assertEqual(len(session.exec(select(MessageResource)).all()), 1)

    def test_unique_conflict_from_a_racing_writer_is_recovered_as_a_replay(self) -> None:
        """A message becoming visible after the replay check cannot duplicate child rows."""

        payload = group_message(message_id=9010)
        self.assertEqual(dispatch_onebot_event(self.engine, settings(), payload), "synced")
        event = GroupMessage.model_validate_json(payload)
        with Session(self.engine) as session, session.begin():
            original_exec = session.exec
            calls = 0

            def exec_with_race(statement: object) -> object:
                nonlocal calls
                calls += 1
                if calls == 2:
                    hidden_before_conflict = MagicMock()
                    hidden_before_conflict.first.return_value = None
                    return hidden_before_conflict
                return original_exec(statement)  # type: ignore[arg-type]

            with patch.object(session, "exec", side_effect=exec_with_race):
                self.assertFalse(persist_group_message(session, event))

        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(Message)).all()), 1)
            self.assertEqual(len(session.exec(select(MessageElement)).all()), 3)
            self.assertEqual(len(session.exec(select(MessageMention)).all()), 1)
            self.assertEqual(len(session.exec(select(MessageResource)).all()), 1)

    def test_reconnect_delay_is_exponential_jittered_and_bounded(self) -> None:
        """Reconnect timing starts at one second in production and cannot exceed its ceiling."""

        production_settings = Settings(
            napcat_host="127.0.0.1",
            napcat_port=3001,
            napcat_path="/",
            napcat_token="safe",
            reconnect_initial_seconds=1,
            reconnect_max_seconds=30,
        )
        self.assertEqual(reconnect_delay(production_settings, 0, random_value=0.2), 1.2)
        self.assertEqual(reconnect_delay(production_settings, 1, random_value=-0.2), 1.6)
        self.assertEqual(reconnect_delay(production_settings, 20, random_value=0.2), 30)


class ServiceLifespanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")

    def test_first_unreachable_connection_terminates_without_token(self) -> None:
        """Initial NapCat failures show only safe endpoint parameters."""

        app = create_app(settings(), engine=self.engine)

        async def start() -> None:
            with patch(
                "src.qqstalker_realtime.app._open_connection",
                new=AsyncMock(side_effect=OSError("unreachable")),
            ):
                with self.assertRaisesRegex(RuntimeError, "host=127.0.0.1 port=3001 path=/") as raised:
                    async with app.router.lifespan_context(app):
                        pass
            self.assertNotIn("test-token-not-for-production", str(raised.exception))

        asyncio.run(start())

    def test_database_failure_stops_before_websocket_connect(self) -> None:
        """The service cannot become ready when PostgreSQL preflight fails."""

        app = create_app(settings(), engine=self.engine)

        async def start() -> None:
            with (
                patch("src.qqstalker_realtime.app._preflight_database", side_effect=OSError()),
                patch("src.qqstalker_realtime.app._open_connection", new=AsyncMock()) as open_connection,
            ):
                with self.assertRaisesRegex(RuntimeError, "PostgreSQL"):
                    async with app.router.lifespan_context(app):
                        pass
                open_connection.assert_not_awaited()
            self.assertFalse(app.state.database_available)

        asyncio.run(start())

    def test_status_endpoints_distinguish_liveness_and_readiness_without_sensitive_data(self) -> None:
        """Operational routes expose state only, not token, messages, or attachment URLs."""

        app = create_app(settings(), engine=self.engine)
        handlers = {
            route.path: route.endpoint
            for route in app.routes
            if isinstance(route, APIRoute)
        }
        self.assertEqual(handlers["/healthz"](), {"status": "ok"})
        with self.assertRaises(HTTPException) as raised:
            handlers["/readyz"]()
        self.assertEqual(raised.exception.status_code, 503)

        app.state.database_available = True
        app.state.connected = True
        self.assertEqual(handlers["/readyz"](), {"status": "ready"})
        status = handlers["/sync/status"]()
        serialized = json.dumps(status, ensure_ascii=False)
        self.assertNotIn("test-token-not-for-production", serialized)
        self.assertNotIn("测试文本", serialized)
        self.assertNotIn("example.invalid", serialized)

    def test_service_syncs_and_reports_simulated_napcat_events_across_a_reconnect(self) -> None:
        """A local NapCat double exercises filtering, replay, recall, failure, and status."""

        initial_socket = EventSocket(
            [
                group_message(message_id=9001),
                group_message(message_id=9003, group_id=10002),
                group_message(message_id=9001),
                group_recall(message_id=9001),
                group_recall(message_id=9999),
                "not-json",
            ]
        )
        resumed_socket = EventSocket([group_message(message_id=9002)], block_after_events=True)
        app = create_app(settings(), engine=self.engine)

        async def start() -> None:
            with patch(
                "src.qqstalker_realtime.app._open_connection",
                new=AsyncMock(return_value=initial_socket),
            ) as open_connection:
                async with app.router.lifespan_context(app):
                    open_connection.side_effect = [resumed_socket]
                    await asyncio.sleep(0.35)
                    self.assertTrue(app.state.first_connected)
                    self.assertTrue(app.state.connected)
                    self.assertEqual(app.state.counts["received"], 7)
                    self.assertEqual(app.state.counts["synced"], 2)
                    self.assertEqual(app.state.counts["duplicate"], 1)
                    self.assertEqual(app.state.counts["recalled"], 1)
                    self.assertEqual(app.state.counts["ignored"], 3)
                    self.assertGreaterEqual(open_connection.await_count, 2)
                    handlers = {
                        route.path: route.endpoint
                        for route in app.routes
                        if isinstance(route, APIRoute)
                    }
                    status = json.dumps(handlers["/sync/status"](), ensure_ascii=False)
                    self.assertNotIn("test-token-not-for-production", status)
                    self.assertNotIn("测试文本", status)
                    self.assertNotIn("example.invalid", status)
            self.assertTrue(initial_socket.closed)
            self.assertTrue(resumed_socket.closed)

        asyncio.run(start())
        with Session(self.engine) as session:
            messages = list(session.exec(select(Message)).all())
            self.assertEqual(len(messages), 2)
            recalled = next(message for message in messages if message.external_id == "9001")
            self.assertTrue(recalled.recalled)


if __name__ == "__main__":
    unittest.main()
