import json

import pytest

from appworld_run import _redact_tool_call_value, reasoning_request
from redaction import Redactor


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("on", {"enabled": True}),
        ("off", {"enabled": False}),
        ("low", {"effort": "low"}),
        ("max", {"effort": "max"}),
    ],
)
@pytest.mark.parametrize("summary", [False, True])
def test_reasoning_request(effort: str, expected: dict[str, bool | str], summary: bool) -> None:
    expected_request = expected.copy()
    if summary:
        expected_request["summary"] = "auto"

    assert reasoning_request(effort, summary) == expected_request


def test_tool_call_redaction_avoids_json_round_trip_failure() -> None:
    secret = r"\nSECRET"
    redactor = Redactor.from_values({"secret": secret})
    tool_calls = [
        {
            "id": "call_1",
            "name": "execute_python",
            "arguments": json.dumps({"code": f'print("{secret}")'}),
        }
    ]

    with pytest.raises(json.JSONDecodeError):
        json.loads(redactor.redact(json.dumps(tool_calls)))

    redacted = _redact_tool_call_value(tool_calls, redactor)

    assert isinstance(redacted, list)
    assert isinstance(redacted[0]["arguments"], str)
    assert secret not in redacted[0]["arguments"]
    assert "<SECRET>" in redacted[0]["arguments"]
    assert json.loads(json.dumps(redacted)) == redacted


def test_tool_call_redaction_preserves_nested_structure() -> None:
    redactor = Redactor.from_values({"secret": "classified"})
    tool_calls = [
        {
            "id": "call_1",
            "arguments": json.dumps({"code": "classified"}),
            "metadata": {
                "classified": "classified",
                "values": [7, True, None, {"text": "classified"}],
            },
        }
    ]

    redacted = _redact_tool_call_value(tool_calls, redactor)

    assert redacted == [
        {
            "id": "call_1",
            "arguments": json.dumps({"code": "<SECRET>"}),
            "metadata": {
                "classified": "<SECRET>",
                "values": [7, True, None, {"text": "<SECRET>"}],
            },
        }
    ]
    assert isinstance(redacted[0]["arguments"], str)
