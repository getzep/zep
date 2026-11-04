import httpx
import pytest

from eog.decide import DECISION_QUESTION, DecisionError, SkillDecider, parse_decision


def test_parse_decision_returns_the_choice_and_the_probabilities() -> None:
    payload = {
        "answers": [
            {
                "name": DECISION_QUESTION,
                "choice": "b",
                "probabilities": [{"value": "a", "probability": 0.2}, {"value": "b", "probability": 0.8}],
            }
        ]
    }
    assert parse_decision(payload, ["a", "b"]) == ("b", {"a": 0.2, "b": 0.8})


def test_parse_decision_takes_the_most_probable_value_without_a_choice_field() -> None:
    payload = {"answers": [{"name": DECISION_QUESTION, "probabilities": [{"value": "a", "probability": 0.9}]}]}
    assert parse_decision(payload, ["a", "b"]) == ("a", {"a": 0.9})


def test_parse_decision_rejects_a_choice_outside_the_candidates() -> None:
    with pytest.raises(DecisionError):
        parse_decision({"answers": [{"name": DECISION_QUESTION, "choice": "z"}]}, ["a", "b"])


def test_decider_posts_one_choice_question_and_sums_usage() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "gpt-6-luna",
                "answers": [{"name": DECISION_QUESTION, "choice": "s2", "probabilities": []}],
                "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    decider = SkillDecider(api_key="test-key", client=client)
    choice, probabilities = decider.decide("Move the meeting", [("s1", "Email"), ("s2", "Calendar")])
    body = __import__("json").loads(requests[0].content)
    assert (choice, probabilities) == ("s2", {})
    assert body["model"] == "gpt-6-luna" and body["input"] == "Move the meeting"
    assert body["questions"][0]["type"] == "choice"
    assert [choice["value"] for choice in body["questions"][0]["choices"]] == ["s1", "s2"]
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert decider.calls == 1 and decider.usage["total_tokens"] == 11


def test_decider_rejects_repeated_candidate_values() -> None:
    decider = SkillDecider(api_key="test-key", client=httpx.Client(transport=httpx.MockTransport(lambda r: None)))
    with pytest.raises(DecisionError):
        decider.decide("task", [("s1", "A"), ("s1", "B")])


def test_decisions_endpoint_follows_openai_base_url(monkeypatch) -> None:
    from eog.decide import decisions_endpoint

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert decisions_endpoint() == "https://api.openai.com/v1/decisions"
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:14100/openai/v1/")
    assert decisions_endpoint() == "http://127.0.0.1:14100/openai/v1/decisions"


def test_parse_decision_returns_none_on_a_refusal() -> None:
    payload = {"answers": [{"type": "refusal", "name": DECISION_QUESTION}]}
    assert parse_decision(payload, ["a", "b"]) == (None, {})
