"""Run the local QQStalker real-time service."""

import uvicorn

from src.qqstalker_realtime.app import create_app
from src.qqstalker_realtime.config import load_settings


def main() -> None:
    """Run the operational API on its configured loopback address."""

    settings = load_settings()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
