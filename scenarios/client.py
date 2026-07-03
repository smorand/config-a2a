"""Test harness clients for the mcp-fs stack.

Two thin HTTP clients, no dependency on the mcp-fs code base (everything goes
over the wire):

* :class:`FsClient` drives the mcp-fs data plane (``/api/fs``): upload EI
  fixtures, list, download generated outputs.
* :class:`AgentClient` drives the config-a2a agent over A2A
  (``/agents/files/message:stream``) and returns the parsed conversation
  (final text, tool-call trace, state).

Both authenticate with a locally minted RS256 user token (the same contract the
production gateway uses), signed with the mcp-fs private test key.

Run directly for a smoke check:  uv run python scenarios/client.py
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import jwt

MCP_FS_URL = os.environ.get("MCP_FS_URL", "http://127.0.0.1:5002").rstrip("/")
AGENT_URL = os.environ.get("AGENT_URL", "http://127.0.0.1:5003/agents/files").rstrip("/")
KEY_PATH = Path(os.environ.get("MCP_FS_KEY", "/Users/sebastien/projects/perso/mcp-fs/.keys/jwt.key"))
EMAIL = os.environ.get("MCP_FS_EMAIL", "sebastien.morand@externe.e-i.com")
MOUNT = os.environ.get("MCP_FS_MOUNT", "perso-seb")


def mint_token(email: str = EMAIL, ttl: int = 3600) -> str:
    """Sign a short-lived RS256 bearer (iss=web-a2a, email claim)."""
    now = int(time.time())
    return jwt.encode(
        {"email": email, "iss": "web-a2a", "iat": now, "exp": now + ttl},
        KEY_PATH.read_text(encoding="utf-8"),
        algorithm="RS256",
    )


def _auth(token: str) -> dict[str, str]:
    return {"X-Forwarded-Authorization": f"Bearer {token}"}


class FsClient:
    """mcp-fs data-plane client (``/api/fs``)."""

    def __init__(self, mount: str = MOUNT, token: str | None = None) -> None:
        self.mount = mount
        self.token = token or mint_token()
        self._c = httpx.Client(base_url=f"{MCP_FS_URL}/api/fs", headers=_auth(self.token), timeout=120)

    def roots(self) -> list[dict[str, Any]]:
        return self._c.get("/roots").raise_for_status().json()["roots"]

    def list(self, path: str = "/") -> list[dict[str, Any]]:
        r = self._c.get(f"/{self.mount}/list", params={"path": path})
        r.raise_for_status()
        return r.json()["entries"]

    def upload(self, local: Path, directory: str = "/", name: str | None = None) -> str:
        files = [("files", (name or local.name, local.read_bytes(), "application/octet-stream"))]
        r = self._c.post(f"/{self.mount}/upload", data={"directory": directory}, files=files)
        r.raise_for_status()
        return r.json()["written"][0]

    def mkdir(self, path: str) -> None:
        self._c.post(f"/{self.mount}/mkdir", json={"path": path}).raise_for_status()

    def delete(self, path: str) -> None:
        self._c.post(f"/{self.mount}/delete", json={"path": path}).raise_for_status()

    def download(self, path: str) -> bytes:
        r = self._c.get(f"/{self.mount}/download", params={"path": path})
        r.raise_for_status()
        return r.content

    def exists(self, path: str) -> bool:
        parent, _, name = path.rstrip("/").rpartition("/")
        try:
            return any(e["name"] == name for e in self.list(parent or "/"))
        except httpx.HTTPStatusError:
            return False


@dataclass
class Turn:
    """Parsed result of one agent conversation turn."""

    state: str = ""
    final_text: str = ""
    tool_calls: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    context_id: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "TASK_STATE_COMPLETED" and bool(self.final_text.strip())


class AgentClient:
    """config-a2a agent client over A2A message:stream."""

    def __init__(self, email: str = EMAIL, agent_url: str = AGENT_URL) -> None:
        self.token = mint_token(email)
        self.agent_url = agent_url

    def converse(self, text: str, context_id: str | None = None, timeout: float = 240) -> Turn:
        body: dict[str, Any] = {
            "message": {"messageId": f"m{int(time.time() * 1000)}", "role": "ROLE_USER", "parts": [{"text": text}]}
        }
        if context_id:
            body["message"]["contextId"] = context_id
        turn = Turn()
        with httpx.Client(timeout=timeout) as c, c.stream(
            "POST", f"{self.agent_url}/message:stream", json=body, headers=_auth(self.token)
        ) as r:
            r.raise_for_status()
            raw = "".join(r.iter_text())
        for block in raw.split("\n\n"):
            for line in block.splitlines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                _absorb(turn, event)
        return turn


def _text_of(message: dict[str, Any] | None) -> str:
    if not message:
        return ""
    return "".join(part.get("text", "") for part in message.get("parts", []))


def _absorb(turn: Turn, event: dict[str, Any]) -> None:
    task = event.get("task")
    if task:
        turn.context_id = task.get("contextId", turn.context_id)
    status_update = event.get("statusUpdate")
    if status_update:
        status = status_update.get("status", {})
        state = status.get("state", "")
        text = _text_of(status.get("message"))
        if state == "TASK_STATE_WORKING" and text.startswith("Tool "):
            turn.tool_calls.append(text.split("→", 1)[0].removeprefix("Tool ").strip())
            turn.thinking.append(text)
        elif state in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED"}:
            turn.state = state
            if text:
                turn.final_text = text
    artifact = event.get("artifactUpdate")
    if artifact:
        turn.final_text += _text_of(artifact.get("artifact"))


if __name__ == "__main__":
    fs = FsClient()
    print("roots:", [r["mount_id"] for r in fs.roots()])
    print("files at /:", [e["name"] for e in fs.list("/")])
    turn = AgentClient().converse("Quels sont les fichiers disponibles ? Réponds en une phrase.")
    print("state:", turn.state, "| tools:", turn.tool_calls)
    print("final:", turn.final_text[:300])
