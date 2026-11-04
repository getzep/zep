from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from langfuse import Langfuse, propagate_attributes

from .dataset import Task

_TRANSCRIPT_LOCK = threading.Lock()


def require_langfuse_env() -> Langfuse:
    values: dict[str, str] = {}
    for name in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"):
        value = os.getenv(name, "")
        if not value:
            raise SystemExit(f"Missing {name}.")
        values[name] = value
    client = Langfuse(
        public_key=values["LANGFUSE_PUBLIC_KEY"],
        secret_key=values["LANGFUSE_SECRET_KEY"],
        base_url=values["LANGFUSE_BASE_URL"],
    )
    if not client.auth_check():
        raise SystemExit("Langfuse authentication failed.")
    return client


class RowTrace:
    def __init__(
        self,
        client: Langfuse,
        *,
        task: Task,
        run_id: str,
        trial: int,
        arm: str,
        model: str,
        effort: str,
        route: str,
        skills_hash: str,
        skills: list[dict[str, str]],
        run_dir: Path,
        skill_index: list[dict[str, str]] | None = None,
    ) -> None:
        self.client = client
        self.task = task
        self.run_id = run_id
        self.task_id = task.task_id
        self.trial = trial
        self.arm = arm
        self.attempt = 0
        self.model = model
        self.effort = effort
        self.route = route
        self.skills_hash = skills_hash
        self.selected_skill_ids = [item["id"] for item in skills]
        self.skill_index = [dict(item) for item in skill_index or []]
        self.run_dir = run_dir
        self.family = task.domain
        self.task_families = self._task_families(task)
        self.name = f"{self.task_id} {self.arm} t{self.trial}"
        self.execution_id = uuid4().hex
        self.trace_id = client.create_trace_id(seed=f"{run_id}:{self.task_id}:{arm}:{trial}:{self.execution_id}")
        self.trace_url = client.get_trace_url(trace_id=self.trace_id)
        self.tags = [
            f"run_id:{run_id}",
            f"family:{self.family}",
            f"arm:{arm}",
            f"model:{model}",
            f"effort:{effort}",
            f"route:{route}",
        ]
        self.metadata = {
            "run_id": run_id,
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "family": self.family,
            "task_families": self.task_families,
            "arm": arm,
            "trial": trial,
            "model": model,
            "effort": effort,
            "route": route,
            "skill_block_sha256": skills_hash,
            "selected_skill_ids": self.selected_skill_ids,
            "skill_index": self.skill_index,
        }
        self._attributes_context = propagate_attributes(tags=self.tags, trace_name=self.name)
        self._attributes_context.__enter__()
        try:
            self._root_context = client.start_as_current_observation(
                name=self.name,
                as_type="span",
                trace_context={"trace_id": self.trace_id},
                metadata=self.metadata,
                end_on_exit=False,
            )
            self.root = self._root_context.__enter__()
        except BaseException as error:
            self._attributes_context.__exit__(type(error), error, error.__traceback__)
            raise
        self.system: str | None = None
        self.user_prompt = task.user_prompt
        self.generations: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.scores: list[dict[str, Any]] = []
        self.score_metadata: dict[str, Any] | None = None
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @staticmethod
    def _task_families(task: Task) -> list[str]:
        from .run import _task_families

        return list(_task_families(task))

    def next_sequence(self) -> int:
        with self._sequence_lock:
            self._sequence += 1
            return self._sequence

    def record_prompt(self, system: str) -> None:
        if self.system is not None:
            return
        self.system = system
        self.root.update(input={"system": system, "user_prompt": self.user_prompt})

    def record_generation(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        turn: Any,
    ) -> None:
        self.record_prompt(system)
        sequence = self.next_sequence()
        tool_calls = [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in turn.tool_calls]
        output = {
            "text": turn.text,
            "reasoning": turn.reasoning_text,
            "tool_calls": tool_calls,
        }
        usage = {
            "input": int(turn.usage.input or 0),
            "cache_read": int(turn.usage.cache_read or 0),
            "cache_write": int(turn.usage.cache_write or 0),
            "output": int(turn.usage.output or 0),
            "reasoning": int(turn.usage.reasoning or 0),
        }
        metadata = {
            "sequence": sequence,
            "attempt": self.attempt,
            "latency_seconds": turn.latency_seconds,
            "served_provider": turn.served_provider,
        }
        generation = self.root.start_observation(
            name=f"model_call_{len(self.generations) + 1}",
            as_type="generation",
            model=self.model,
            input=[{"role": "system", "content": system}, *messages],
            output=output,
            usage_details=usage,
            metadata=metadata,
        )
        generation.end()
        self.generations.append(
            {
                "sequence": sequence,
                "attempt": self.attempt,
                "name": f"model_call_{len(self.generations) + 1}",
                "model": self.model,
                "input": [{"role": "system", "content": system}, *messages],
                "output": output,
                "usage": usage,
                "metadata": metadata,
            }
        )

    def record_error(self, error: Exception) -> None:
        sequence = self.next_sequence()
        message = f"{type(error).__name__}: {error}"
        self.root.start_observation(
            name=f"model_call_error_{sequence}",
            as_type="event",
            output={"error_class": type(error).__name__, "error": str(error)},
            metadata={"sequence": sequence, "attempt": self.attempt},
            level="ERROR",
            status_message=message,
        )
        self.events.append(
            {
                "sequence": sequence,
                "attempt": self.attempt,
                "name": f"model_call_error_{sequence}",
                "error_class": type(error).__name__,
                "error": str(error),
            }
        )

    def start_tool(self, name: str, arguments: dict[str, Any]) -> tuple[int, Any]:
        sequence = self.next_sequence()
        observation = self.root.start_observation(
            name=f"tool:{name}",
            as_type="tool",
            input=arguments,
            metadata={"sequence": sequence, "attempt": self.attempt},
        )
        return sequence, observation

    def record_tool(
        self,
        *,
        sequence: int,
        name: str,
        arguments: dict[str, Any],
        result: dict[str, Any] | None,
        error: str | None,
        observation: Any,
    ) -> None:
        is_error = error is not None or bool(result and result.get("isError"))
        status_message = "Tool returned an error result" if is_error else None
        if error is not None:
            status_message = error
        observation.update(
            output=result if error is None else {"error": error},
            level="ERROR" if is_error else "DEFAULT",
            status_message=status_message,
        )
        observation.end()
        self.tool_calls.append(
            {
                "sequence": sequence,
                "attempt": self.attempt,
                "name": f"tool:{name}",
                "arguments": arguments,
                "result": result,
                "is_error": is_error,
                "error": error or (status_message if is_error else None),
            }
        )

    def add_scores(
        self,
        *,
        reward: int,
        verifier: Any,
        corrected_reward: int | None = None,
        corrected_verifier: Any | None = None,
        score_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._create_score("reward", reward, None)
        for index, passed in enumerate(verifier.passed):
            self._create_score(
                f"verifier_{index}",
                int(passed),
                {
                    "actual": verifier.actual[index],
                    "expected": verifier.expected[index],
                    "error": verifier.errors[index],
                },
            )
        if corrected_reward is not None:
            self._create_score("corrected_reward", corrected_reward, None)
            if corrected_verifier is not None:
                for index, passed in enumerate(corrected_verifier.passed):
                    self._create_score(
                        f"corrected_verifier_{index}",
                        int(passed),
                        {
                            "actual": corrected_verifier.actual[index],
                            "expected": corrected_verifier.expected[index],
                            "error": corrected_verifier.errors[index],
                        },
                    )
        if score_metadata is not None:
            self.score_metadata = score_metadata
            self.metadata["score_metadata"] = score_metadata
            self.root.update(metadata=self.metadata)

    def _create_score(self, name: str, value: int, details: dict[str, Any] | None) -> None:
        comment = json.dumps(details, sort_keys=True, default=str) if details is not None else None
        self.client.create_score(
            name=name,
            value=value,
            trace_id=self.trace_id,
            data_type="NUMERIC",
            comment=comment,
        )
        self.scores.append({"name": name, "value": value, "comment": comment})

    def close(self, row_output: dict[str, Any] | None) -> dict[str, Any]:
        if self._closed:
            return self._output_fields()
        output = row_output or {}
        self._closed = True
        try:
            try:
                self.root.update(
                    output={
                        "status": output.get("status"),
                        "finish_reason": output.get("finish_reason"),
                        "turns": output.get("turns"),
                        "model_calls": output.get("model_calls"),
                        "read_skill_calls": output.get("read_skill_calls", []),
                    }
                )
            finally:
                try:
                    self.root.end()
                finally:
                    try:
                        self._root_context.__exit__(None, None, None)
                    finally:
                        self._attributes_context.__exit__(None, None, None)
            transcript = {
                "run_id": self.run_id,
                "task_id": self.task_id,
                "family": self.family,
                "arm": self.arm,
                "trial": self.trial,
                "execution_id": self.execution_id,
                "attempt": self.attempt,
                "model": self.model,
                "effort": self.effort,
                "route": self.route,
                "trace_id": self.trace_id,
                "trace_url": self.trace_url,
                "skill_block_sha256": self.skills_hash,
                "selected_skill_ids": self.selected_skill_ids,
                "skill_index": self.skill_index,
                "read_skill_calls": output.get("read_skill_calls", []),
                "system": self.system,
                "user_prompt": self.user_prompt,
                "generations": self.generations,
                "tool_calls": self.tool_calls,
                "events": self.events,
                "scores": self.scores,
                **({"score_metadata": self.score_metadata} if self.score_metadata is not None else {}),
                "status": output.get("status"),
                "finish_reason": output.get("finish_reason"),
                "reward": output.get("reward", 0),
            }
            transcript_path = self.run_dir / "transcripts.jsonl"
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            with _TRANSCRIPT_LOCK, transcript_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(transcript, sort_keys=True, default=str) + "\n")
        finally:
            self.client.flush()
        return self._output_fields()

    def _output_fields(self) -> dict[str, Any]:
        final_attempt_generations = sum(item["attempt"] == self.attempt for item in self.generations)
        return {
            "langfuse_trace_id": self.trace_id,
            "langfuse_trace_url": self.trace_url,
            "langfuse_generations": final_attempt_generations,
            "langfuse_generations_all_attempts": len(self.generations),
        }


class TracedRoute:
    def __init__(self, inner: Any, row_trace: RowTrace) -> None:
        self.inner = inner
        self.row_trace = row_trace

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def complete(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], **kwargs: Any) -> Any:
        try:
            turn = self.inner.complete(system, messages, tools, **kwargs)
        except Exception as error:
            self.row_trace.record_error(error)
            raise
        self.row_trace.record_generation(system=system, messages=messages, turn=turn)
        return turn


class TracedMCP:
    def __init__(self, inner: Any, row_trace: RowTrace) -> None:
        self.inner = inner
        self.row_trace = row_trace

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        sequence, observation = self.row_trace.start_tool(name, arguments)
        try:
            result = self.inner.call_tool(name, arguments)
        except Exception as error:
            self.row_trace.record_tool(
                sequence=sequence,
                name=name,
                arguments=arguments,
                result=None,
                error=f"{type(error).__name__}: {error}",
                observation=observation,
            )
            raise
        self.row_trace.record_tool(
            sequence=sequence,
            name=name,
            arguments=arguments,
            result=result,
            error=None,
            observation=observation,
        )
        return result
