from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from harbor.environments.base import BaseEnvironment


@dataclass(frozen=True)
class BridgeInfo:
    host: str
    port: int
    token: str
    log_path: Path

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


class ExecBridge:
    def __init__(
        self,
        environment: BaseEnvironment,
        loop: asyncio.AbstractEventLoop,
        token: str,
        log_path: Path,
        bind_host: str = "127.0.0.1",
        public_host: str | None = None,
        default_cwd: str = "/app",
        max_output_chars: int = 120_000,
    ):
        self.environment = environment
        self.loop = loop
        self.token = token
        self.log_path = log_path
        self.bind_host = bind_host
        self.public_host = public_host or (
            "127.0.0.1" if bind_host == "0.0.0.0" else bind_host
        )
        self.default_cwd = default_cwd
        self.max_output_chars = max_output_chars
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "DumateHarborExecBridge/0.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:
                if self.path != "/health":
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(200, {"ok": True})

            def do_POST(self) -> None:
                if self.path != "/exec":
                    self._send_json(404, {"error": "not found"})
                    return
                if not self._authorized():
                    self._send_json(401, {"error": "unauthorized"})
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw_body = self.rfile.read(length)
                    payload = json.loads(raw_body.decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError("request body must be a JSON object")
                    response = bridge.execute(payload)
                    self._send_json(200, response)
                except Exception as exc:
                    bridge.write_log(
                        {
                            "event": "bridge_error",
                            "error": repr(exc),
                            "ts": time.time(),
                        }
                    )
                    self._send_json(500, {"error": str(exc)})

            def _authorized(self) -> bool:
                header = self.headers.get("Authorization", "")
                return header == f"Bearer {bridge.token}"

            def _send_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer((bind_host, 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="dumate-harbor-exec-bridge",
            daemon=True,
        )

    @property
    def info(self) -> BridgeInfo:
        return BridgeInfo(
            host=self.public_host,
            port=self._server.server_address[1],
            token=self.token,
            log_path=self.log_path,
        )

    def start(self) -> BridgeInfo:
        self._thread.start()
        info = self.info
        self.write_log(
            {
                "event": "bridge_started",
                "bind_host": self.bind_host,
                "public_host": info.host,
                "port": info.port,
                "ts": time.time(),
            }
        )
        return info

    def stop(self) -> None:
        self.write_log({"event": "bridge_stopping", "ts": time.time()})
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("field 'command' must be a non-empty string")

        cwd = payload.get("cwd", self.default_cwd)
        if cwd is not None and not isinstance(cwd, str):
            raise ValueError("field 'cwd' must be a string or null")

        timeout_sec = payload.get("timeout_sec", 120)
        if timeout_sec is not None:
            timeout_sec = int(timeout_sec)

        env = payload.get("env")
        if env is not None and not isinstance(env, dict):
            raise ValueError("field 'env' must be an object or null")

        started = time.time()
        self.write_log(
            {
                "event": "exec_start",
                "command": command,
                "cwd": cwd,
                "timeout_sec": timeout_sec,
                "ts": started,
            }
        )

        future = asyncio.run_coroutine_threadsafe(
            self.environment.exec(
                command=command,
                cwd=cwd,
                env=env,
                timeout_sec=timeout_sec,
            ),
            self.loop,
        )
        result = future.result(timeout=(timeout_sec + 10) if timeout_sec else None)
        elapsed = time.time() - started
        response = {
            "stdout": self._truncate(result.stdout),
            "stderr": self._truncate(result.stderr),
            "return_code": result.return_code,
            "elapsed_sec": round(elapsed, 3),
        }
        self.write_log(
            {
                "event": "exec_finish",
                "command": command,
                "cwd": cwd,
                "return_code": result.return_code,
                "elapsed_sec": round(elapsed, 3),
                "stdout": self._truncate(result.stdout, 20_000),
                "stderr": self._truncate(result.stderr, 20_000),
                "ts": time.time(),
            }
        )
        return response

    def _truncate(self, value: str | None, limit: int | None = None) -> str:
        if not value:
            return ""
        limit = limit or self.max_output_chars
        if len(value) <= limit:
            return value
        omitted = len(value) - limit
        return f"{value[:limit]}\n...[truncated {omitted} chars]"

    def write_log(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
