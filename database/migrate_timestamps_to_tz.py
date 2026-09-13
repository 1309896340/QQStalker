"""把 public schema 下所有不带时区的 timestamp 列迁移为 timestamptz。

历史列类型为 `timestamp without time zone`，而应用写入的一直是 UTC 值
（`datetime.now(UTC)`）。用 `AT TIME ZONE 'UTC'` 转换可保持时刻不变，
迁移后客户端（如 DBeaver）会按会话/本地时区显示。仅处理 public schema
中本应用的数据表，脚本可重复执行（已转换的列会被跳过）。

用法：uv run python database/migrate_timestamps_to_tz.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_dotenv(env_path: Path) -> None:
    """Load simple KEY=VALUE settings without overwriting explicit environment values."""

    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv(REPO_ROOT / ".env")

from sqlalchemy import text  # noqa: E402

from src.qqstalker_core.database import create_database_engine  # noqa: E402

ALTER_STATEMENT = (
    "ALTER TABLE {table} ALTER COLUMN {column} TYPE timestamptz "
    "USING {column} AT TIME ZONE 'UTC'"
)
LOCK_TIMEOUT = "30s"


def main() -> None:
    engine = create_database_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        naive_columns = conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND data_type = 'timestamp without time zone' "
                "ORDER BY table_name, column_name"
            )
        ).all()
        if not naive_columns:
            print("没有需要迁移的时间列，所有时间列均已带时区。")
            return

        print(f"待转换 {len(naive_columns)} 列（lock_timeout={LOCK_TIMEOUT}）：")
        for table_name, column_name in naive_columns:
            print(f"  {table_name}.{column_name} -> timestamptz")
            conn.exec_driver_sql(
                ALTER_STATEMENT.format(table=table_name, column=column_name)
            )

        converted = conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND data_type = 'timestamp with time zone' "
                "ORDER BY table_name, column_name"
            )
        ).all()
        print(f"完成：本次转换 {len(naive_columns)} 列，当前带时区时间列共 {len(converted)} 列：")
        for table_name, column_name in converted:
            print(f"  {table_name}.{column_name} = timestamptz")

        sample = conn.execute(
            text(
                "SELECT peer_uid, name, created_at AT TIME ZONE 'Asia/Shanghai' "
                "AS created_at_local FROM chats ORDER BY created_at"
            )
        ).all()
        print("chats.created_at 按本地时区（Asia/Shanghai）抽样：")
        for peer_uid, name, created_at_local in sample:
            print(f"  {peer_uid} {name}: {created_at_local}")


if __name__ == "__main__":
    main()
