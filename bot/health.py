"""Render (and other PaaS) health-check servers.

A free Render web service is killed if it does not bind at least one port.
Telegram long polling is outbound-only, so we run a tiny HTTP server on $PORT
in a background thread purely to satisfy the port scan.
"""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        self.do_GET()

    def log_message(self, *args):  # silence per-request logging
        pass


def start_health_server(port: int) -> None:
    try:
        server = HTTPServer(("0.0.0.0", port), _Health)
    except OSError as exc:
        log.warning("Health server could not bind port %d: %s", port, exc)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Health server listening on port %d", port)
