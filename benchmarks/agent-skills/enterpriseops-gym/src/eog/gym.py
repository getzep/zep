from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .dataset import Task
from .errors import InfrastructureError

SEED_TIMEOUT = 300.0
SQL_TIMEOUT = 60.0
SQL_ERROR_MARKERS = (
    "invalid sql",
    "sql error",
    "verifier query",
    "query error",
    "schema error",
    "no such table",
    "no such column",
    "syntax error",
    "unrecognized token",
    "ambiguous column",
    "incomplete input",
)


def _is_sql_query_error(response: httpx.Response) -> bool:
    if response.status_code not in {400, 422}:
        return False
    try:
        body = json.dumps(response.json()).lower()
    except ValueError:
        body = response.text.lower()
    return any(marker in body for marker in SQL_ERROR_MARKERS)


def _verifier_http_error(response: httpx.Response) -> str:
    digest = hashlib.sha256(response.content).hexdigest()
    return f"status={response.status_code} error_class=HTTPStatusError body_sha256={digest}"


@dataclass(frozen=True, slots=True)
class VerifierResult:
    passed: tuple[bool, ...]
    actual: tuple[Any, ...]
    expected: tuple[Any, ...]
    errors: tuple[str | None, ...]

    @property
    def success(self) -> bool:
        return all(self.passed)


def context_headers(context: dict[str, Any] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in (context or {}).items():
        name = key.strip().lower()
        if not name.startswith("x-"):
            name = f"x-{name}"
        headers[name.replace("_", "-")] = str(value)
    return headers


def extract_sql_value(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if "data" in payload:
        rows = payload["data"]
    elif "rows" in payload:
        rows = payload["rows"]
    elif "result" in payload:
        return extract_sql_value(payload["result"])
    else:
        return payload
    if not isinstance(rows, list) or not rows:
        return rows
    if len(rows) == 1 and isinstance(rows[0], dict):
        if len(rows[0]) == 1:
            return next(iter(rows[0].values()))
        return rows[0]
    if len(rows) == 1 and isinstance(rows[0], list) and len(rows[0]) == 1:
        return rows[0][0]
    return rows


def compare_values(actual: Any, expected: Any, comparison_type: str = "equals") -> bool:
    try:
        if comparison_type == "equals":
            return actual == expected
        if comparison_type == "greater_than":
            return actual > expected
        if comparison_type == "less_than":
            return actual < expected
        if comparison_type == "contains":
            return expected in str(actual)
    except (TypeError, ValueError):
        return False
    return False


class GymClient:
    def __init__(
        self,
        base_url: str,
        *,
        name: str = "",
        context: dict[str, Any] | None = None,
        timeout: float = SQL_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.context = context or {}
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout, transport=transport)

    def health(self) -> dict[str, Any]:
        try:
            response = self.client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise InfrastructureError(f"Gym health check failed for {self.name or self.base_url}") from exc

    def seed(self, database_id: str, sql: str) -> dict[str, Any]:
        body = {
            "database_id": database_id,
            "name": database_id,
            "description": "EnterpriseOps-Gym benchmark episode",
            "sql_content": sql,
        }
        try:
            response = self.client.post(
                f"{self.base_url}/api/seed-database",
                json=body,
                headers=context_headers(self.context),
                timeout=SEED_TIMEOUT,
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise InfrastructureError(f"Gym seed failed for {self.name or self.base_url}") from exc

    def delete(self, database_id: str) -> None:
        try:
            response = self.client.request(
                "DELETE",
                f"{self.base_url}/api/delete-database",
                json={"database_id": database_id},
                headers=context_headers(self.context),
                timeout=SQL_TIMEOUT,
            )
            response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as exc:
            raise InfrastructureError(f"Gym database deletion failed for {self.name or self.base_url}") from exc

    def run_sql(self, database_id: str, query: str) -> Any:
        try:
            response = self.client.post(
                f"{self.base_url}/api/sql-runner",
                json={"database_id": database_id, "query": query},
                headers={"x-database-id": database_id, **context_headers(self.context)},
                timeout=SQL_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            raise InfrastructureError(f"Gym SQL query failed for {self.name or self.base_url}") from exc

    def verify(self, task: Task, database_ids: dict[str, str]) -> VerifierResult:
        passed: list[bool] = []
        actual_values: list[Any] = []
        expected_values: list[Any] = []
        errors: list[str | None] = []
        configs = {str(item.get("mcp_server_name", "")): item for item in task.gym_servers_config}
        for verifier in task.verifiers:
            validation = verifier.get("validation_config", {})
            gym_name = str(verifier.get("gym_name") or self.name)
            database_id = database_ids.get(gym_name)
            if not database_id:
                raise InfrastructureError(f"No database ID for gym server {gym_name}")
            config = configs.get(gym_name, {})
            client = self
            if config and config.get("mcp_server_url", "").rstrip("/") != self.base_url:
                client = GymClient(
                    config["mcp_server_url"],
                    name=gym_name,
                    context=config.get("context", {}),
                    timeout=self.timeout,
                )
            expected = validation.get("expected_value")
            try:
                response = client.client.post(
                    f"{client.base_url}/api/sql-runner",
                    json={"database_id": database_id, "query": validation["query"]},
                    headers={"x-database-id": database_id, **context_headers(client.context)},
                    timeout=SQL_TIMEOUT,
                )
                if _is_sql_query_error(response):
                    passed.append(False)
                    actual_values.append(None)
                    expected_values.append(expected)
                    errors.append(_verifier_http_error(response))
                    continue
                response.raise_for_status()
                result = extract_sql_value(response.json())
                comparison = validation.get("comparison_type", "equals")
                passed.append(compare_values(result, expected, comparison))
                actual_values.append(result)
                expected_values.append(expected)
                errors.append(None)
            except KeyError as exc:
                raise InfrastructureError(f"Invalid verifier for task {task.task_id}") from exc
            except (httpx.HTTPError, TimeoutError, ValueError) as exc:
                raise InfrastructureError(f"Gym SQL query failed for {gym_name}") from exc
            finally:
                if client is not self:
                    client.close()
        return VerifierResult(tuple(passed), tuple(actual_values), tuple(expected_values), tuple(errors))

    def check_reset(
        self,
        task: Task,
        database_ids: dict[str, str],
        baseline_path: Path | None = None,
    ) -> VerifierResult:
        result = self.verify(task, database_ids)
        path = baseline_path or Path(__file__).parents[2] / "data" / "noop_baseline.jsonl"
        expected = None
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if row["task_id"] == task.task_id:
                expected = tuple(row["passes"])
                break
        if expected is None:
            raise InfrastructureError(f"No no-op baseline for task {task.task_id}")
        if result.passed != expected:
            raise InfrastructureError(f"Reset check failed for task {task.task_id}")
        return result

    def close(self) -> None:
        self.client.close()


def image_digest(container: str) -> str:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", container],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise InfrastructureError(f"Could not inspect Docker image for {container}") from exc
    return result.stdout.strip()
