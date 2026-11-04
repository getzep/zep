from __future__ import annotations

import json
from contextlib import suppress
from typing import Any
from uuid import uuid4

import httpx

from .dataset import Task
from .errors import InfrastructureError
from .gym import context_headers

MCP_TIMEOUT = 60.0
_TOOL_NAME_ALIASES = {"list_team_apps": "list_teams_apps"}


class MCPClient:
    def __init__(
        self,
        endpoint: str,
        *,
        database_id: str,
        context: dict[str, Any] | None = None,
        timeout: float = MCP_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.database_id = database_id
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout, transport=transport)
        self.headers = {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "x-database-id": database_id,
            **context_headers(context),
        }
        self.session_id: str | None = None
        self._request_id = 0
        self._tools: list[dict[str, Any]] | None = None

    def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        headers = dict(self.headers)
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        try:
            response = self.client.post(self.endpoint, json=payload, headers=headers, timeout=self.timeout)
            response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as exc:
            raise InfrastructureError("MCP transport failed") from exc
        self.session_id = response.headers.get("mcp-session-id", self.session_id)
        if not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    body = line[5:].strip()
                    if body and body != "[DONE]":
                        return json.loads(body)
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise InfrastructureError("MCP returned invalid JSON") from exc

    def _request(self, method: str, params: dict[str, Any] | None = None, *, tool_call: bool = False) -> dict[str, Any]:
        self._request_id += 1
        response = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params or {},
            }
        )
        if not isinstance(response, dict):
            raise InfrastructureError(f"MCP returned no result for {method}")
        if "error" in response:
            if method == "tools/call" and tool_call:
                error = response["error"]
                message = error.get("message", "Unknown error") if isinstance(error, dict) else str(error)
                return {
                    "content": [{"type": "text", "text": f"Error: {message}"}],
                    "isError": True,
                }
            raise InfrastructureError(f"MCP protocol error for {method}")
        return response.get("result", {})

    def initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "enterpriseops-gym-skills", "version": "0.1.0"},
            },
        )
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict[str, Any]]:
        if self._tools is not None:
            return self._tools
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        self._tools = tools
        return tools

    def tools_for(self, task: Task) -> list[dict[str, Any]]:
        available = {tool["name"]: tool for tool in self.list_tools()}
        selected = []
        for name in task.selected_tools:
            remote_name = name if name in available else _TOOL_NAME_ALIASES.get(name, name)
            if remote_name in available:
                selected.append({**available[remote_name], "name": name, "_remote_tool_name": remote_name})
        return selected

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._request("tools/call", {"name": name, "arguments": arguments}, tool_call=True)
        except InfrastructureError:
            raise
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise InfrastructureError(f"MCP tool call failed: {name}") from exc
        return result

    def close(self) -> None:
        if self.session_id:
            with suppress(InfrastructureError):
                self._post(
                    {
                        "jsonrpc": "2.0",
                        "id": f"close-{uuid4()}",
                        "method": "notifications/cancelled",
                        "params": {"requestId": 0, "reason": "session complete"},
                    }
                )
        self.client.close()
