from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def load_example(monkeypatch: pytest.MonkeyPatch, filename: str) -> Any:
    monkeypatch.setenv("ZEP_API_KEY", "test-zep-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    path = Path(__file__).parents[1] / "examples" / filename
    spec = importlib.util.spec_from_file_location(f"livekit_{filename}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename", ["voice_assistant.py", "graph_voice_assistant.py"])
def test_examples_build_responses_llm(
    filename: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_example(monkeypatch, filename)
    llm = module.build_llm()

    assert isinstance(llm, module.openai.responses.LLM)
    assert llm._opts.model == "gpt-6-luna"
    assert llm._opts.reasoning.effort == "low"
