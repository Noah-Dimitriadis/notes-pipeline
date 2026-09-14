"""Placeholder for the api service until M13 adds notes_pipeline/webapi.py.

Standard-library only, deliberately: this container's Dockerfile is real
work (Python 3.13, ffmpeg, poppler, whisper.cpp built with Vulkan) so its
GPU-access acceptance criterion is testable now, but the actual FastAPI +
FastMCP app is M13's job. This just proves the container runs, keeps the
compose service up, and answers on the right interface.
"""

from __future__ import annotations

import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8000


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - required override name
        if self.path == "/healthz":
            self._respond(200, b"ok\n")
        elif self.path == "/healthz/gpu":
            self._respond_gpu()
        else:
            self._respond(404, b"placeholder api - see M13\n")

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_gpu(self) -> None:
        try:
            result = subprocess.run(
                ["whisper-cli", "--help"], capture_output=True, timeout=10, text=True
            )
            ok = result.returncode == 0
            body = (result.stdout + result.stderr).encode()
            self._respond(200 if ok else 500, body)
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim for on-box debugging
            self._respond(500, str(exc).encode())

    def log_message(self, fmt: str, *args: object) -> None:  # quiet, this is a placeholder
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
