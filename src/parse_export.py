"""Parse a QQChatExporter/NapCat export without persisting it yet."""

from __future__ import annotations

import argparse
from pathlib import Path

import orjson

from src.schemas import QQChatExport


def parse_export(json_path: Path) -> QQChatExport:
    """Read one exporter JSON document with orjson and validate it with Pydantic."""

    return QQChatExport.model_validate(orjson.loads(json_path.read_bytes()))


def default_images_dir(json_path: Path) -> Path:
    return json_path.parent / "resources" / "images"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("json_path", type=Path, help="Path to the exported QQ JSON file")
    parser.add_argument(
        "--images-dir",
        type=Path,
        help="Image directory (defaults to <json parent>/resources/images)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of parsed messages to select for the pending processing step",
    )
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    if args.limit < 0:
        raise SystemExit("--limit must be zero or greater")
    if not args.json_path.is_file():
        raise SystemExit(f"JSON export does not exist: {args.json_path}")

    images_dir = args.images_dir or default_images_dir(args.json_path)
    if not images_dir.is_dir():
        raise SystemExit(f"Image resources directory does not exist: {images_dir}")

    export = parse_export(args.json_path)
    selected_messages = export.messages[: args.limit]
    image_file_count = sum(1 for entry in images_dir.iterdir() if entry.is_file())

    print(
        f"Parsed {len(export.messages)} messages from {args.json_path.name}; "
        f"selected {len(selected_messages)}; found {image_file_count} image resources."
    )

    # TODO: Normalize selected_messages and persist them with the SQLModel ORM.


if __name__ == "__main__":
    main()
