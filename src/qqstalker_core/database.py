"""PostgreSQL connection helpers shared by QQStalker applications."""

import os

from sqlalchemy import Engine, URL
from sqlmodel import create_engine


def database_url_from_environment() -> str | URL:
    """Build a psycopg URL from DATABASE_URL or Compose environment settings."""

    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url
    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("Set DATABASE_URL or POSTGRES_PASSWORD before connecting.")
    return URL.create(
        "postgresql+psycopg",
        username=os.getenv("POSTGRES_USER", "qqstalker"),
        password=password,
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "qqstalker"),
    )


def create_database_engine() -> Engine:
    """Create a checked PostgreSQL engine for CLI and service use."""

    return create_engine(
        database_url_from_environment(), pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )
