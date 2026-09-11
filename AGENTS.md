# Repository Guidelines

## Project Structure & Module Organization

The application package is `src/`. `models/chat.py` contains SQLModel entities, while `schemas/qq_export.py` defines Pydantic models for QQChatExporter/NapCat data. Command-line workflows live alongside them: `import_export.py` imports archives, `export_markdown.py` writes transcripts, `analyze_transcript.py` calls the configured LLM, and `render_html_png.py` produces PNG output. Keep database runtime assets in `database/` and design notes in `docs/proposals/`. Generated logs, exports, analyses, and local data are ignored by Git.

## Build, Test, and Development Commands

Use Python 3.12 and `uv` from the repository root:

```powershell
uv sync                                  # install locked dependencies
uv run python -m src.import_export --help
uv run python -m src.export_markdown --help
uv run pyright                           # static type checking
uv run python -m compileall -q src       # syntax compilation check
```

Run PostgreSQL when exercising import/export workflows:

```powershell
docker compose -f database/docker-compose.yml up -d
```

Use module invocations (`python -m src.import_export`), rather than executing files by path, so absolute `src.*` imports resolve correctly.

## Coding Style & Naming Conventions

Follow existing Python conventions: four-space indentation, UTF-8 source, `snake_case` for functions and variables, `PascalCase` for classes, and explicit type annotations on public functions. Prefer `pathlib.Path` for filesystem inputs and `argparse` for CLI options. Keep ORM fields and schema aliases aligned with exporter names; use a trailing `# type: ignore` only where an external library’s typing requires it. Keep user-facing messages clear and Chinese, matching current scripts.

## Testing Guidelines

No automated test suite or coverage threshold is configured yet. Add focused tests under `tests/` for parsing, batching, date filtering, and database synchronization changes; name files `test_<feature>.py` and test functions `test_<behavior>`. Use minimal, anonymized export fixtures and never commit chat archives. Run the relevant tests plus `uv run pyright` and `uv run python -m compileall -q src` before opening a pull request.

## Commit & Pull Request Guidelines

Recent history follows Conventional Commit-style messages, for example `feat(render): export HTML portraits as PNG segments` and `fix(import): harden database synchronization`. Use `feat`, `fix`, `docs`, or `chore` with a concise scope. Pull requests should describe the affected workflow, link related issues when available, list verification commands, and include sample sanitized output or screenshots for export/rendering changes.

## Security & Configuration

Copy `.env.example` to `.env` for local settings. Never commit, print, or share `.env`, API keys, database passwords, or unredacted conversation data. Treat LLM-generated portraits as interpretations, not verified facts.
