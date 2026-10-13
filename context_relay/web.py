"""`relay ui`: the Context Relay window as a local web page.

Useful when you can't see this Mac's screen (e.g. driving it remotely through T3 Code):
open http://localhost:<port> in any browser or preview panel that can reach this machine.
Binds to 127.0.0.1 only.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import handoff, hooks

DEFAULT_PORT = 47821
_PAGE = Path(__file__).with_name("ui.html")
_cache = {"at": 0.0, "data": None}
_lock = threading.Lock()


def status(fresh: bool = False) -> dict:
    with _lock:
        if fresh or not _cache["data"] or time.time() - _cache["at"] > 10:
            projects = handoff.projects()
            for p in projects:
                p["has_context"] = (Path(p["path"]) / ".relay" / "CONTEXT.md").exists()
            _cache.update(at=time.time(), data={"version": 1, "hooks": hooks.status(), "projects": projects})
        return _cache["data"]


def _readable(path: str) -> bool:
    """Only relay's own Markdown files (CONTEXT.md and transcripts) may be read through the UI."""
    real = os.path.realpath(path)
    return real.endswith(".md") and "/.relay/" in real and os.path.isfile(real)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (
            json.dumps(body).encode() if ctype == "application/json" else str(body).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            return self._send(200, _PAGE.read_bytes(), "text/html")
        if url.path == "/api/status":
            return self._send(200, status(fresh="fresh" in parse_qs(url.query)))
        if url.path == "/api/file":
            path = (parse_qs(url.query).get("path") or [""])[0]
            if not _readable(path):
                return self._send(403, {"ok": False, "error": "not a relay file"})
            return self._send(200, Path(path).read_text(encoding="utf-8", errors="replace"), "text/plain")
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"ok": False, "error": "bad json"})
        url = urlparse(self.path)
        try:
            if url.path == "/api/continue":
                return self._send(200, _continue(body.get("agent"), body.get("project")))
            if url.path == "/api/hooks":
                result = hooks.install() if body.get("action") == "install" else hooks.uninstall()
                status(fresh=True)
                return self._send(200, {"ok": True, "hooks": result})
        except Exception as e:  # report to the page instead of dropping the connection
            return self._send(500, {"ok": False, "error": str(e)})
        self._send(404, {"ok": False, "error": "not found"})


def _continue(agent: str, project: str) -> dict:
    if agent not in ("claude", "codex", "cursor") or not project or not os.path.isdir(project):
        return {"ok": False, "error": "unknown agent or project"}
    h = handoff.prepare(project, agent, force=True)
    if not h:
        return {"ok": False, "error": f"No agent sessions found for {project}"}
    if agent == "cursor":
        handoff.write_cursor_rule(h)
    status(fresh=True)
    return {
        "ok": True,
        "from_agent": h.session.agent,
        "from_name": h.session.name,
        "title": handoff.title(h.session),
        "context": str(h.context),
        "transcript": str(h.transcript),
        "prompt": handoff.cursor_prompt(h) if agent == "cursor" else handoff.kickoff_prompt(h),
    }


class _IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve(port: int = DEFAULT_PORT) -> None:
    # Loopback only, but on both IPv4 and IPv6: "localhost" resolves to ::1 for many clients/proxies.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    try:
        v6 = _IPv6Server(("::1", port), Handler)
        threading.Thread(target=v6.serve_forever, daemon=True).start()
    except OSError:
        pass  # no IPv6 loopback on this machine
    print(f"Context Relay UI: http://localhost:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
