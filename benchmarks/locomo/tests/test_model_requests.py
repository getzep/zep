import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import prompts
from common import CompletenessGrade, Grade
from config import BenchmarkConfig


def request_runner():
    # Isolate request methods from the legacy Zep graph SDK imports.
    source = Path(__file__).parents[1] / "evaluation.py"
    tree = ast.parse(source.read_text())
    runner = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    runner.body = [
        node
        for node in runner.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"_generate_response", "_grade_response", "evaluate_context_completeness"}
    ]
    namespace = {**vars(prompts), "Grade": Grade, "CompletenessGrade": CompletenessGrade}
    exec(compile(ast.Module(body=[runner], type_ignores=[]), str(source), "exec"), namespace)
    return namespace["EvaluationRunner"]()


@pytest.mark.asyncio
async def test_luna_response_and_graders_use_low_reasoning():
    runner = request_runner()
    runner.config = BenchmarkConfig()
    runner.openai = MagicMock()
    runner.openai.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="A fictional answer"))]
        )
    )
    runner.openai.beta.chat.completions.parse = AsyncMock(
        return_value=SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        parsed=SimpleNamespace(
                            is_correct="correct",
                            reasoning="A mocked grade",
                            completeness="complete",
                            missing_elements=[],
                            present_elements=["answer"],
                        )
                    )
                )
            ],
        )
    )
    assert (
        await runner._generate_response("Fictional context", "A fictional question")
        == "A fictional answer"
    )
    await runner._grade_response("A fictional question", "A fictional answer", "A fictional answer")
    await runner.evaluate_context_completeness(
        "Fictional context", "A fictional question", "A fictional answer"
    )
    calls = [runner.openai.chat.completions.create.call_args]
    calls.extend(runner.openai.beta.chat.completions.parse.call_args_list)
    assert len(calls) == 3
    for call in calls:
        assert call.kwargs["model"] == "gpt-6-luna"
        assert call.kwargs["reasoning_effort"] == "low"
        assert "temperature" not in call.kwargs
        assert "tools" not in call.kwargs
