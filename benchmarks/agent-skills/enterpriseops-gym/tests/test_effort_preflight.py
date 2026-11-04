from types import SimpleNamespace

from eog.effort_preflight import FIXED_PROMPT, run_effort_preflight
from eog.providers.deepseek_route import DEEPSEEK_MODEL


def test_preflight_sends_fixed_reasoning_prompt_three_times_per_effort() -> None:
    class RecordingRoute:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[dict[str, str]], list[dict], str]] = []

        def complete(self, system, messages, tools, *, effort, **kwargs):
            self.calls.append((system, messages, tools, effort))
            return SimpleNamespace(
                usage=SimpleNamespace(reasoning={"low": 1, "medium": 2, "max": 3}[effort], output=4),
                latency_seconds=0.1,
            )

    route = RecordingRoute()
    report = run_effort_preflight(route, DEEPSEEK_MODEL)

    assert FIXED_PROMPT == (
        "A school has 4 boxes of pencils. Each box contains 3 packs, and each pack contains 8 pencils. "
        "The school gives 17 pencils to each of 5 classes, then receives 9 more pencils. "
        "How many pencils remain? Show the calculation and the final number."
    )
    assert [effort for _, _, _, effort in route.calls] == [
        "low",
        "low",
        "low",
        "medium",
        "medium",
        "medium",
        "max",
        "max",
        "max",
    ]
    assert all(
        system == "You are a concise assistant."
        and messages == [{"role": "user", "content": FIXED_PROMPT}]
        and tools == []
        for system, messages, tools, _ in route.calls
    )
    assert len(report["rows"]) == 9
    assert report["passed"]
