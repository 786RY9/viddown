"""``python -m viddown_server`` — production entry point (used by systemd).

SIGTERM/SIGINT start a graceful drain: the app refuses new jobs immediately
(503 + Retry-After, /healthz -> 503), uvicorn stops accepting connections and
waits up to VIDDOWN_DRAIN_TIMEOUT_S (default 300 s) for running streams to
finish before cancelling them. systemd's TimeoutStopSec must exceed that.
"""

from __future__ import annotations

import logging
import sys
from types import FrameType

import uvicorn
from fastapi import FastAPI

from .app import create_app
from .config import Settings


class DrainingServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, app: FastAPI) -> None:
        super().__init__(config)
        self.app = app

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        svc = getattr(self.app.state, "services", None)
        if svc is not None:
            svc.admission.start_drain()
        super().handle_exit(sig, frame)


def main() -> int:
    s = Settings.from_env()
    logging.basicConfig(
        level=s.log_level,
        stream=sys.stderr,  # journald captures it
        format="%(levelname)s %(name)s: %(message)s",
    )
    app = create_app(s)
    config = uvicorn.Config(
        app,
        host=s.host,
        port=s.port,
        proxy_headers=False,  # we resolve X-Forwarded-For ourselves (security.client_ip)
        server_header=False,
        access_log=False,  # no IPs in logs
        timeout_graceful_shutdown=s.drain_timeout_s,
        timeout_keep_alive=15,
        log_config=None,
    )
    DrainingServer(config, app).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
