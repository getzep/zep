from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from strands.models.openai_responses import OpenAIResponsesModel


def load_example(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("ZEP_API_KEY", "test-zep-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    path = Path(__file__).parents[1] / "examples" / "basic_agent.py"
    spec = importlib.util.spec_from_file_location("strands_example", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_example_formats_low_reasoning_responses_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_example(monkeypatch)
    model = module.build_model()

    assert isinstance(model, OpenAIResponsesModel)
    request = model._format_request([])
    assert request["model"] == "gpt-6-luna"
    assert request["reasoning"]["effort"] == "low"
