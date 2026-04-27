from __future__ import annotations

import asyncio
import json
import secrets
import socket
import textwrap
import time
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from dumate_harbor_tb2.dumate_client import (
    DEFAULT_HOST,
    DEFAULT_LOG_PATH,
    DEFAULT_PORT,
    DumateClient,
    resolve_base_url,
    save_dumate_exchange,
)
from dumate_harbor_tb2.exec_bridge import ExecBridge


class DumateAgent(BaseAgent):
    """Harbor agent adapter that lets Dumate operate Harbor's task container."""

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        log_path: str | None = None,
        workspace: str | None = None,
        session_title_prefix: str = "harbor-tb2",
        request_timeout_sec: int = 30,
        message_timeout_sec: int = 900,
        poll_after_send_sec: int = 120,
        bridge_bind_host: str = "0.0.0.0",
        bridge_public_host: str | None = None,
        bridge_default_cwd: str = "/app",
        extra_prompt: str | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        extra_env = extra_env or {}
        host = extra_env.get("DUMATE_HOST", host)
        port = int(
            extra_env.get("OPENCODE_SERVER_PORT", extra_env.get("DUMATE_PORT", port))
        )
        log_path = extra_env.get("DUMATE_LOG_PATH", log_path or "")
        workspace = extra_env.get("DUMATE_WORKSPACE", workspace or "")
        bridge_bind_host = extra_env.get("DUMATE_BRIDGE_BIND_HOST", bridge_bind_host)
        bridge_public_host = extra_env.get(
            "DUMATE_BRIDGE_PUBLIC_HOST",
            bridge_public_host or "",
        )
        self.host = host
        self.port = port
        self.logs_dir = self.logs_dir.expanduser().resolve()
        self.log_path = (
            Path(log_path).expanduser().resolve() if log_path else DEFAULT_LOG_PATH
        )
        self.workspace = (
            Path(workspace).expanduser().resolve()
            if workspace
            else (self.logs_dir / "workspace").resolve()
        )
        self.session_title_prefix = session_title_prefix
        self.request_timeout_sec = int(request_timeout_sec)
        self.message_timeout_sec = int(message_timeout_sec)
        self.poll_after_send_sec = int(poll_after_send_sec)
        self.bridge_bind_host = bridge_bind_host
        self.bridge_public_host = bridge_public_host or None
        self.bridge_default_cwd = bridge_default_cwd
        self.extra_prompt = extra_prompt
        self.extra_env = extra_env

    @staticmethod
    def name() -> str:
        return "dumate"

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        loop = asyncio.get_running_loop()
        token = secrets.token_urlsafe(24)
        bridge = ExecBridge(
            environment=environment,
            loop=loop,
            token=token,
            log_path=self.logs_dir / "bridge.jsonl",
            bind_host=self.bridge_bind_host,
            public_host=self.bridge_public_host,
            default_cwd=self.bridge_default_cwd,
        )
        bridge_info = bridge.start()
        bridge_urls = self._candidate_bridge_urls(bridge_info.port, bridge_info.host)
        self._write_helper(bridge_urls, bridge_info.token)

        endpoint = resolve_base_url(
            host=self.host,
            default_port=self.port,
            log_path=self.log_path,
        )
        client = DumateClient(
            base_url=endpoint.base_url,
            workspace=self.workspace,
            request_timeout_sec=self.request_timeout_sec,
            message_timeout_sec=self.message_timeout_sec,
        )
        prompt = self._build_prompt(
            instruction=instruction,
            bridge_url=bridge_info.base_url,
            bridge_urls=bridge_urls,
            token=bridge_info.token,
        )

        session_data: dict[str, Any] | None = None
        response_data: dict[str, Any] | None = None
        messages: list[dict[str, Any]] = []
        try:
            session_data = await asyncio.to_thread(
                client.create_session,
                self.session_title_prefix,
            )
            session_id = session_data["id"]

            # requests blocks; keep Harbor's event loop free so bridge exec calls run.
            response_data = await asyncio.to_thread(
                client.send_message,
                session_id,
                prompt,
            )
            messages = await self._poll_messages(client, session_id, bridge_info.log_path)
        finally:
            try:
                if session_data is not None and response_data is not None:
                    saved = save_dumate_exchange(
                        output_dir=self.logs_dir / "dumate",
                        session_data=session_data,
                        prompt=prompt,
                        response_data=response_data,
                        messages=messages,
                        endpoint=endpoint,
                    )
                    self._write_run_metadata(
                        endpoint=endpoint.base_url,
                        endpoint_source=endpoint.source,
                        session_id=session_data["id"],
                        bridge_url=bridge_info.base_url,
                        bridge_urls=bridge_urls,
                        saved=saved,
                    )
                    context.metadata = {
                        **(context.metadata or {}),
                        "dumate_session_id": session_data["id"],
                        "dumate_base_url": endpoint.base_url,
                        "dumate_port_source": endpoint.source,
                        "bridge_url": bridge_info.base_url,
                        "bridge_urls": bridge_urls,
                        "bridge_log": str(bridge_info.log_path),
                        "dumate_logs": {
                            key: str(value) for key, value in saved.items()
                        },
                    }
            finally:
                bridge.stop()

    async def _poll_messages(
        self,
        client: DumateClient,
        session_id: str,
        bridge_log_path: Path,
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + self.poll_after_send_sec
        messages: list[dict[str, Any]] = []
        while True:
            messages = await asyncio.to_thread(client.fetch_messages, session_id)
            if self._has_assistant_text(messages) and self._bridge_saw_exec(
                bridge_log_path
            ):
                return messages
            if time.monotonic() >= deadline:
                return messages
            await asyncio.sleep(5)

    def _has_assistant_text(self, messages: list[dict[str, Any]]) -> bool:
        for message in messages:
            if message.get("info", {}).get("role") != "assistant":
                continue
            for part in message.get("parts", []):
                if part.get("type") == "text" and (part.get("text") or "").strip():
                    return True
        return False

    def _bridge_saw_exec(self, bridge_log_path: Path) -> bool:
        if not bridge_log_path.exists():
            return False
        return '"event": "exec_' in bridge_log_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

    def _candidate_bridge_urls(self, port: int, public_host: str) -> list[str]:
        hosts = [public_host, "127.0.0.1", "host.docker.internal"]
        host_ip = self._detect_host_ip()
        if host_ip:
            hosts.append(host_ip)

        urls: list[str] = []
        seen: set[str] = set()
        for host in hosts:
            if not host or host == "0.0.0.0":
                continue
            url = f"http://{host}:{port}"
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    def _detect_host_ip(self) -> str | None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except OSError:
            return None

    def _write_helper(self, bridge_urls: list[str], token: str) -> None:
        helper_path = self.workspace / "hb_exec.py"
        helper_path.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import json
                import sys
                import urllib.request

                BRIDGE_URLS = {bridge_urls!r}
                TOKEN = {token!r}

                def main():
                    if len(sys.argv) < 2:
                        print("usage: python hb_exec.py '<shell command>'", file=sys.stderr)
                        return 2
                    payload = {{
                        "command": " ".join(sys.argv[1:]),
                        "cwd": "/app",
                        "timeout_sec": 120,
                    }}
                    data = json.dumps(payload).encode("utf-8")
                    last_error = None
                    result = None
                    for bridge_url in BRIDGE_URLS:
                        request = urllib.request.Request(
                            bridge_url + "/exec",
                            data=data,
                            method="POST",
                            headers={{
                                "Content-Type": "application/json",
                                "Authorization": "Bearer " + TOKEN,
                            }},
                        )
                        try:
                            with urllib.request.urlopen(request, timeout=180) as response:
                                result = json.loads(response.read().decode("utf-8"))
                            break
                        except Exception as exc:
                            last_error = exc
                    if result is None:
                        print(f"failed to reach Harbor bridge: {{last_error}}", file=sys.stderr)
                        return 111
                    if result.get("stdout"):
                        print(result["stdout"], end="")
                    if result.get("stderr"):
                        print(result["stderr"], end="", file=sys.stderr)
                    return int(result.get("return_code", 1))

                if __name__ == "__main__":
                    raise SystemExit(main())
                """
            ),
            encoding="utf-8",
        )

    def _build_prompt(
        self,
        instruction: str,
        bridge_url: str,
        bridge_urls: list[str],
        token: str,
    ) -> str:
        helper_path = self.workspace / "hb_exec.py"
        extra = f"\n\nAdditional instruction:\n{self.extra_prompt}" if self.extra_prompt else ""
        bridge_url_list = "\n".join(f"              - {url}" for url in bridge_urls)
        return textwrap.dedent(
            f"""\
            You are solving a Terminal-Bench 2.0 task under Harbor.

            Important execution model:
            - The real task files and final deliverables are inside Harbor's Linux task environment.
            - The task working directory inside that environment is /app.
            - Do not start Docker yourself. Do not run docker pull, docker run, or docker exec.
            - Your local shell is only a control plane. To inspect or modify task files, execute shell commands through the Harbor bridge.
            - Harbor will run the official verifier after you finish. You do not need to run /tests/test.sh unless you want to self-check.

            Preferred command helper:
            - A helper script has been created in your session workspace:
              {helper_path}
            - If your shell starts in that workspace, use:
              python hb_exec.py 'pwd && ls -la /app'
            - Run commands like:
              python {helper_path} 'pwd && ls -la /app'
              python {helper_path} 'cat > /app/answer.txt <<'"'"'EOF'"'"'
              content
              EOF'

            Raw bridge API, if needed:
            Candidate bridge URLs:
{bridge_url_list}

            curl -sS -X POST {bridge_url}/exec \\
              -H 'Content-Type: application/json' \\
              -H 'Authorization: Bearer {token}' \\
              --data '{{"command":"ls -la /app","cwd":"/app","timeout_sec":120}}'

            Solve the task now. Make all required file changes under /app through the bridge. When finished, reply briefly that the task is complete.

            Task instruction:
            {instruction}
            {extra}
            """
        )

    def _write_run_metadata(
        self,
        endpoint: str,
        endpoint_source: str,
        session_id: str,
        bridge_url: str,
        bridge_urls: list[str],
        saved: dict[str, Path],
    ) -> None:
        metadata = {
            "agent": self.name(),
            "version": self.version(),
            "dumate_base_url": endpoint,
            "dumate_port_source": endpoint_source,
            "session_id": session_id,
            "bridge_url": bridge_url,
            "bridge_urls": bridge_urls,
            "saved": {key: str(path) for key, path in saved.items()},
        }
        (self.logs_dir / "run-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
