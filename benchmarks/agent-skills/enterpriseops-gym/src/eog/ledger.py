from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from .errors import BudgetExceeded
from .prices import cost_usd, prompt_token_count


class JsonlLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, row: dict[str, Any]) -> None:
        data = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with self._lock:
            self._append_data(data)

    def _append_data(self, data: bytes) -> None:
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    def append_failure(self, row: dict[str, Any]) -> None:
        self.append(row)


class CostLedger(JsonlLedger):
    def __init__(self, path: Path, *, max_usd: float | None = None) -> None:
        super().__init__(path)
        self.max_usd = max_usd
        self.total_usd = sum(float(row["usd"]) for row in self.rows() if row.get("usd") is not None)

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def check_budget(self) -> None:
        with self._lock:
            if self.max_usd is not None and self.total_usd > self.max_usd:
                raise BudgetExceeded(f"Model-call cost exceeded the configured limit of ${self.max_usd:.2f}")

    def append_call(self, row: dict[str, Any]) -> dict[str, Any]:
        cost = cost_usd(
            row["model"],
            input_tokens=int(row.get("input_tokens", 0)),
            cache_read_tokens=int(row.get("cache_read_tokens", 0)),
            cache_write_tokens=int(row.get("cache_write_tokens", 0)),
            output_tokens=int(row.get("output_tokens", 0)),
            prompt_tokens=int(
                row.get(
                    "prompt_tokens",
                    prompt_token_count(
                        row["model"],
                        input_tokens=int(row.get("input_tokens", 0)),
                        cache_read_tokens=int(row.get("cache_read_tokens", 0)),
                        cache_write_tokens=int(row.get("cache_write_tokens", 0)),
                    ),
                )
            ),
        )
        record = {**row, "usd": cost}
        data = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with self._lock:
            self._append_data(data)
            if cost is None and self.max_usd is not None:
                raise BudgetExceeded("Cannot enforce the configured cost limit without a model price")
            if cost is not None:
                self.total_usd += cost
            if self.max_usd is not None and self.total_usd > self.max_usd:
                raise BudgetExceeded(f"Model-call cost exceeded the configured limit of ${self.max_usd:.2f}")
        return record
