from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4096
DEFAULT_LOG_PATH = (
    Path.home() / "Library/Application Support/qianfan-desktop-app/log/electron.log"
)


@dataclass(frozen=True)
class DumateEndpoint:
    base_url: str
    port: int
    source: str


def resolve_base_url(
    host: str = DEFAULT_HOST,
    default_port: int = DEFAULT_PORT,
    log_path: Path = DEFAULT_LOG_PATH,
) -> DumateEndpoint:
    """Resolve the active Dumate/opencode HTTP server URL."""
    env_port = os.getenv("OPENCODE_SERVER_PORT", "").strip()
    if env_port.isdigit():
        port = int(env_port)
        return DumateEndpoint(
            base_url=f"http://{host}:{port}",
            port=port,
            source="env(OPENCODE_SERVER_PORT)",
        )

    if log_path.exists():
        content = log_path.read_text(encoding="utf-8", errors="ignore")
        candidates: list[tuple[int, int, str]] = []
        patterns = [
            (r'"opencodePort":\s*(\d+)', "log(opencodePort)"),
            (r'"Dumate-agt-Port":\s*(\d+)', "log(Dumate-agt-Port)"),
            (r"Dumate-agt started on port (\d+)", "log(Dumate-agt started)"),
            (r"Starting: .* on port (\d+)", "log(Starting on port)"),
        ]
        for pattern, source in patterns:
            for match in re.finditer(pattern, content):
                try:
                    candidates.append((match.end(), int(match.group(1)), source))
                except (IndexError, ValueError):
                    continue

        if candidates:
            _, port, source = max(candidates, key=lambda item: item[0])
            return DumateEndpoint(
                base_url=f"http://{host}:{port}",
                port=port,
                source=f"{source} @ {log_path}",
            )

    return DumateEndpoint(
        base_url=f"http://{host}:{default_port}",
        port=default_port,
        source="default_port",
    )


class DumateClient:
    def __init__(
        self,
        base_url: str,
        workspace: Path,
        request_timeout_sec: int = 30,
        message_timeout_sec: int = 900,
    ):
        self.base_url = base_url.rstrip("/")
        self.workspace = workspace
        self.request_timeout_sec = request_timeout_sec
        self.message_timeout_sec = message_timeout_sec

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-opencode-directory": str(self.workspace),
        }

    def create_session(self, title: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "title": title,
            "permission": [{"permission": "*", "pattern": "*", "action": "allow"}],
        }
        response = requests.post(
            f"{self.base_url}/session",
            json=payload,
            headers=self._headers,
            timeout=self.request_timeout_sec,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("id"):
            raise RuntimeError(f"Dumate session response has no id: {data}")
        return data

    def send_message(self, session_id: str, text: str) -> dict[str, Any]:
        payload = {"parts": [{"type": "text", "text": text}]}
        response = requests.post(
            f"{self.base_url}/session/{session_id}/message",
            json=payload,
            headers=self._headers,
            timeout=self.message_timeout_sec,
        )
        response.raise_for_status()
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        return {
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "body": body,
        }

    def fetch_messages(self, session_id: str) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.base_url}/session/{session_id}/message",
            headers={"x-opencode-directory": str(self.workspace)},
            timeout=self.request_timeout_sec,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError(
                f"Dumate messages response should be a list, got {type(data).__name__}"
            )
        return data


def messages_to_readable(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, message in enumerate(messages, 1):
        info = message.get("info", {})
        role = info.get("role", "unknown")
        message_id = info.get("id", "")
        lines.append(f"=== Message {index} | role={role} | id={message_id} ===")
        for part in message.get("parts", []):
            part_type = part.get("type", "unknown")
            if part_type in {"text", "reasoning"}:
                lines.append(f"[{part_type}]")
                lines.append(part.get("text", ""))
            elif part_type == "tool":
                tool = part.get("tool", {})
                tool_name = tool.get("name") if isinstance(tool, dict) else None
                lines.append(f"[tool] {tool_name or '(unknown)'}")
            elif part_type == "file":
                lines.append(f"[file] {part.get('url', '')}")
            else:
                lines.append(f"[{part_type}]")
        lines.append("")
    return "\n".join(lines)


def save_dumate_exchange(
    output_dir: Path,
    session_data: dict[str, Any],
    prompt: str,
    response_data: dict[str, Any],
    messages: list[dict[str, Any]],
    endpoint: DumateEndpoint,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    session_id = session_data["id"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{timestamp}_{session_id}"

    paths = {
        "session": output_dir / f"{prefix}_session.json",
        "request": output_dir / f"{prefix}_request.json",
        "response": output_dir / f"{prefix}_response.json",
        "messages": output_dir / f"{prefix}_messages.json",
        "readable": output_dir / f"{prefix}_messages_readable.txt",
        "summary": output_dir / f"{prefix}_summary.md",
    }

    paths["session"].write_text(
        json.dumps(session_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["request"].write_text(
        json.dumps(
            {
                "endpoint": {
                    "base_url": endpoint.base_url,
                    "port": endpoint.port,
                    "source": endpoint.source,
                },
                "parts": [{"type": "text", "text": prompt}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    paths["response"].write_text(
        json.dumps(response_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["messages"].write_text(
        json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    paths["readable"].write_text(messages_to_readable(messages), encoding="utf-8")

    assistant_texts: list[str] = []
    for message in messages:
        if message.get("info", {}).get("role") != "assistant":
            continue
        for part in message.get("parts", []):
            if part.get("type") == "text":
                text = (part.get("text") or "").strip()
                if text:
                    assistant_texts.append(text)

    summary = [
        "# Dumate Harbor TB2 Run",
        "",
        f"- session_id: `{session_id}`",
        f"- base_url: `{endpoint.base_url}`",
        f"- port_source: `{endpoint.source}`",
        f"- message_count: `{len(messages)}`",
        f"- created_at: `{datetime.now().isoformat(timespec='seconds')}`",
        "",
        "## Assistant Text",
        "",
    ]
    if assistant_texts:
        for index, text in enumerate(assistant_texts, 1):
            summary.append(f"### Reply {index}")
            summary.append("")
            summary.append(text)
            summary.append("")
    else:
        summary.append("No assistant text content.")
    paths["summary"].write_text("\n".join(summary), encoding="utf-8")

    return paths
