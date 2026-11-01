import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from banking import Database, expected_database, make_case
from runner import TEACHING_REQUEST, build_agent, episode, model_identifier, trace_events


@pytest.mark.parametrize(
    ("model_name", "expected"),
    [
        ("claude-haiku-5-5", ("anthropic", "claude-haiku-5-5")),
        ("claude-opus-5-5", ("anthropic", "claude-opus-5-5")),
        ("gpt-6-luna", ("openai", "gpt-6-luna")),
        ("gpt-4.1-mini", ("openai", "gpt-4.1-mini")),
        ("o3-mini", ("openai", "o3-mini")),
        ("openai:custom-model", ("openai", "custom-model")),
        ("anthropic:custom-model", ("anthropic", "custom-model")),
    ],
)
def test_model_provider_and_name_are_selected_without_network_calls(model_name, expected):
    assert model_identifier(model_name) == expected


@pytest.mark.parametrize("model_name", ["unknown-model", "openai:", "other:custom-model"])
def test_unsupported_model_identifiers_are_rejected(model_name):
    with pytest.raises(ValueError, match="anthropic:MODEL or openai:MODEL"):
        model_identifier(model_name)


@pytest.mark.parametrize("failure", ["invalid-model", "provider-setup"])
def test_model_setup_failures_save_artifacts_and_close_the_client(tmp_path, monkeypatch, failure):
    import httpx

    import runner

    clients = []

    class TrackedClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            clients.append(self)

    def fail_provider(**kwargs):
        raise RuntimeError("The provider setup failed.")

    monkeypatch.setattr(runner.httpx, "AsyncClient", TrackedClient)
    monkeypatch.setattr(runner, "AsyncAnthropic", fail_provider)
    model = "unknown-model" if failure == "invalid-model" else "claude-test"
    path = tmp_path / failure
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(episode(path, make_case(0), model_name=model))
    assert len(clients) == 1 and clients[0].is_closed
    assert (path / "failure.json").exists()
    assert json.loads((path / "provider-calls.json").read_text()) == []
    assert json.loads((path / "messages.json").read_text()) == []
    assert json.loads((path / "database.json").read_text()) == {"credits": [], "reports": []}
    assert not (path / "result.json").exists()


def scripted_model(operations, seen=None):
    iterator = iter(operations)

    def respond(_messages, info):
        if seen is not None:
            seen.append([t.parameters_json_schema for t in info.function_tools])
        item = next(iterator, None)
        if item is None:
            return ModelResponse(
                parts=[TextPart("The account review is complete.")], finish_reason="stop"
            )
        name, args = item
        return ModelResponse(
            parts=[ToolCallPart(name, args, tool_call_id=f"call-{len(_messages)}")],
            finish_reason="tool_call",
        )

    return FunctionModel(respond)


def valid_operations(case):
    db = expected_database(case)
    operations = [
        ("get_case", {}),
        ("list_documents", {}),
        ("read_document", {"document_id": "monthly-interest"}),
        ("calculator", {"expression": "1.005"}),
    ]
    operations.append(
        (
            "compare_packages",
            {
                "packages": [
                    report.model_dump(
                        mode="json", include={"account_id", "checking_account_id", "card_id"}
                    )
                    for report in db.reports
                ]
            },
        )
    )
    for credit, report in zip(db.credits, db.reports, strict=True):
        operations.append(("credit_interest", {"account_id": credit.account_id}))
        operations.append(
            (
                "file_discrepancy_report",
                {
                    "account_id": report.account_id,
                    "correction_credit_id": report.correction_credit_id,
                },
            )
        )
    return operations


def test_framework_persists_and_scores_complete_db(tmp_path):
    case = make_case(0)
    path = tmp_path / "valid"
    result = asyncio.run(episode(path, case, test_model=scripted_model(valid_operations(case))))
    assert result["passed"]
    assert json.loads((path / "start.json").read_text())["model"] == "claude-haiku-5-5"
    assert result["validation_corrections"] == 0
    assert len(json.loads((path / "database.json").read_text())["reports"]) == 4
    events = trace_events(path)
    call = next(
        e
        for e in events
        if e["event_type"] == "tool_call" and e["context"]["tools"][0]["name"] == "credit_interest"
    )
    assert json.loads(call["content"])["account_id"] == case.savings[0].account_id
    assert "arguments" not in json.loads(call["content"])
    assert sum(e["event_type"] == "tool_result" for e in events) == 13


def test_four_refused_quotes_do_not_consume_validation_retries_or_mutate_state(tmp_path):
    case = make_case(2)
    args = {
        "account_id": case.savings[0].account_id,
        "checking_account_id": case.checking[-1].account_id,
        "card_id": case.cards[-1].card_id,
    }
    path = tmp_path / "refused"
    result = asyncio.run(
        episode(
            path,
            case,
            test_model=scripted_model([("quote_interest", args)] * 4 + valid_operations(case)),
        )
    )
    assert result["passed"] and result["validation_corrections"] == 0
    quotes = json.loads((path / "tool-calls.json").read_text())[:4]
    assert all(q["result"]["permitted"] is False for q in quotes)


def test_unknown_savings_quote_is_a_recoverable_tool_error(tmp_path):
    case = make_case(2)
    result = asyncio.run(
        episode(
            tmp_path / "unknown",
            case,
            test_model=scripted_model(
                [("quote_interest", {"account_id": "missing"})] + valid_operations(case)
            ),
        )
    )
    assert result["passed"] and result["validation_corrections"] == 1


def test_teaching_request_is_only_in_the_teacher_prompt(tmp_path):
    case = make_case(0)
    for teaching in (False, True):
        path = tmp_path / str(teaching)
        asyncio.run(episode(path, case, test_model=scripted_model([]), teaching=teaching))
        events = trace_events(path)
        prompt = next(e["content"] for e in events if e["event_type"] == "input_reference")
        assert (TEACHING_REQUEST in prompt) is teaching
        assert json.loads((path / "start.json").read_text())["teaching"] is teaching


def test_invalid_credit_request_is_visible_and_does_not_mutate(tmp_path):
    case = make_case(0)
    ops = [
        (
            "credit_interest",
            {"request": {}},
        )
    ]
    path = tmp_path / "validation"
    result = asyncio.run(episode(path, case, test_model=scripted_model(ops)))
    assert not result["passed"]
    assert result["validation_corrections"] == 1
    assert json.loads((path / "database.json").read_text()) == {"credits": [], "reports": []}


def test_disabled_thinking_omits_unsupported_sampling_parameters():
    case = make_case(0)
    model = scripted_model([])
    student = build_agent(case, model, "none", [], Database(), [])
    teacher = build_agent(case, model, "max", [], Database(), [])
    assert isinstance(student.model_settings, dict)
    assert not {"temperature", "top_p", "top_k"}.intersection(student.model_settings)
    assert student.model_settings.get("anthropic_thinking") == {"type": "disabled"}
    assert isinstance(teacher.model_settings, dict)
    assert "temperature" not in teacher.model_settings


def test_both_arms_expose_identical_schemas():
    case, catalog = (
        make_case(0),
        [
            {
                "uuid": "skill-example",
                "name": "Example",
                "version": 1,
                "markdown": "An invented test procedure.",
            }
        ],
    )
    schemas = []
    for skills in ([], catalog):
        seen = []
        agent = build_agent(case, scripted_model([], seen), "low", skills, Database(), [])
        agent.run_sync("Review this invented test case.")
        schemas.append(seen[0])
    assert schemas[0] == schemas[1]
    assert len(schemas[0]) == 9


def test_treatment_requires_actual_skill_read(tmp_path):
    skills = [{"uuid": "skill-example", "version": 1, "markdown": "Example procedure."}]
    with pytest.raises(RuntimeError, match="did not read"):
        asyncio.run(
            episode(tmp_path / "unread", make_case(0), skills=skills, test_model=scripted_model([]))
        )
    assert (tmp_path / "unread" / "failure.json").exists()


def test_episode_cannot_overwrite_results(tmp_path):
    path = tmp_path / "existing"
    asyncio.run(episode(path, make_case(0), test_model=scripted_model([])))
    with pytest.raises(FileExistsError):
        asyncio.run(episode(path, make_case(0), test_model=scripted_model([])))
