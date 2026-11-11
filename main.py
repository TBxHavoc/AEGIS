"""AEGIS application entry point."""
from __future__ import annotations

import logging
import signal
from threading import Event, Thread

from backend.api.server import AegisServer
from backend.configuration.settings import Settings
from backend.core.logging_config import configure_logging


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    log = logging.getLogger(__name__)
    server = AegisServer(settings)
    stopping = Event()

    def stop(signum: int, _frame: object) -> None:
        log.info("Shutdown requested by signal %s", signum)
        if not stopping.is_set():
            stopping.set()
            Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    log.info("AEGIS listening on http://%s:%d", settings.host, settings.port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        log.info("AEGIS stopped")


if __name__ == "__main__":
    main()

