"""Tests for QQChatExporter archive discovery and sequential imports."""

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import cast
import unittest
from unittest.mock import call, patch

from sqlalchemy import Engine
from src.qqstalker_cli import import_export
from src.qqstalker_core.schemas.qq_export import QQChatExport


class ExportDiscoveryTests(unittest.TestCase):
    def test_returns_json_exports_in_case_insensitive_filename_order(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            archive_dir = Path(temporary_directory)
            for filename in ("z-last.json", "A-first.JSON", "notes.txt", "middle.json"):
                (archive_dir / filename).touch()

            json_paths = import_export.find_export_jsons(archive_dir)

        self.assertEqual(
            [path.name for path in json_paths],
            ["A-first.JSON", "middle.json", "z-last.json"],
        )

    def test_rejects_an_archive_without_json_exports(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            archive_dir = Path(temporary_directory)

            with self.assertRaisesRegex(ValueError, "Expected at least one JSON export"):
                import_export.find_export_jsons(archive_dir)


class SequentialImportTests(unittest.TestCase):
    def test_imports_each_json_in_order_and_aggregates_counts(self) -> None:
        json_paths = [Path("first.json"), Path("second.json")]
        images_dir = Path("resources/images")
        engine = cast(Engine, object())

        with patch(
            "src.qqstalker_cli.import_export.synchronize_export",
            side_effect=[(2, 1), (3, 4)],
        ) as synchronize_export:
            imported, skipped = import_export.synchronize_exports(json_paths, images_dir, engine)

        self.assertEqual((imported, skipped), (5, 5))
        self.assertEqual(
            synchronize_export.call_args_list,
            [
                call(json_paths[0], images_dir, engine),
                call(json_paths[1], images_dir, engine),
            ],
        )


class SkipAlreadyImportedTests(unittest.TestCase):
    def write_export(self, temporary_directory: str) -> Path:
        json_path = Path(temporary_directory) / "group.json"
        json_path.write_bytes(b'{"messages": []}')
        return json_path

    def test_skips_parsing_when_an_identical_export_was_already_imported(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            json_path = self.write_export(temporary_directory)
            engine = cast(Engine, object())
            recorded_batch = SimpleNamespace(completed_at=datetime.now(UTC))

            with (
                patch("src.qqstalker_cli.import_export.Session") as session_factory,
                patch.object(import_export.SQLModel.metadata, "create_all"),
                patch.object(
                    import_export.QQChatExport,
                    "model_validate",
                    side_effect=AssertionError("already imported exports must not be parsed"),
                ),
            ):
                session = session_factory.return_value.__enter__.return_value
                session.exec.return_value.first.return_value = recorded_batch

                imported, skipped = import_export.synchronize_export(
                    json_path,
                    Path(temporary_directory) / "missing-images",
                    engine,
                )

        self.assertEqual((imported, skipped), (0, 0))

    def test_proceeds_when_no_completed_batch_was_recorded(self) -> None:
        for recorded_batch in (None, SimpleNamespace(completed_at=None)):
            with self.subTest(recorded_batch=recorded_batch):
                with TemporaryDirectory() as temporary_directory:
                    json_path = self.write_export(temporary_directory)
                    engine = cast(Engine, object())

                    with (
                        patch("src.qqstalker_cli.import_export.Session") as session_factory,
                        patch.object(import_export.SQLModel.metadata, "create_all"),
                    ):
                        session = session_factory.return_value.__enter__.return_value
                        session.exec.return_value.first.return_value = recorded_batch

                        with self.assertRaisesRegex(
                            FileNotFoundError, "Image resources directory"
                        ):
                            import_export.synchronize_export(
                                json_path,
                                Path(temporary_directory) / "missing-images",
                                engine,
                            )
