from types import SimpleNamespace

from episode_audit import EpisodeAudit, reasoning_parts


def test_reasoning_extracts_only_returned_text():
    assert reasoning_parts(
        [
            {
                "type": "reasoning",
                "summary": [{"type": "summary_text", "text": "Summary."}],
                "content": [{"type": "reasoning_text", "text": "Reasoning."}],
                "encrypted_content": "opaque",
            },
            {
                "type": "anthropic_content",
                "content": [
                    {"type": "thinking", "thinking": "Thinking.", "signature": "opaque"},
                    {"type": "redacted_thinking", "data": "opaque"},
                ],
            },
            {"reasoning_content": "Provider reasoning."},
        ]
    ) == [
        {"kind": "summary", "text": "Summary."},
        {"kind": "reasoning_content", "text": "Reasoning."},
        {"kind": "thinking", "text": "Thinking."},
        {"kind": "reasoning_content", "text": "Provider reasoning."},
    ]


def test_empty_reasoning_does_not_invent_a_summary():
    assert reasoning_parts([]) == []
    assert (
        reasoning_parts(
            [
                {"type": "reasoning", "summary": [], "content": None},
                {"type": "reasoning", "summary": [{"text": ""}]},
                {"type": "anthropic_content", "content": [{"type": "thinking", "thinking": ""}]},
            ]
        )
        == []
    )


def _call(name, call_id):
    return {"type": "function_call", "name": name, "call_id": call_id}


def test_audit_preserves_internal_requests_and_all_tool_positions():
    audit = EpisodeAudit()
    turn = audit.start_turn()
    request = audit.start_request(turn, "requested")
    audit.record_response(turn, request, [_call("get_current_time", "clock")])
    turn = audit.start_turn()
    request = audit.start_request(turn, "requested")
    audit.record_response(turn, request, [_call("read_skill", "read")])
    audit.record_skill_read("read", "procedure", {"found": True, "repaired": True})
    request = audit.start_request(turn, "requested")
    audit.record_response(
        turn,
        request,
        [
            {"type": "reasoning", "summary": [{"text": "Use the KB."}]},
            _call("KB_search_dense", "search"),
        ],
    )
    messages = [
        SimpleNamespace(role="assistant", usage=None),
        SimpleNamespace(role="assistant", usage={}),
        SimpleNamespace(
            role="user",
            tool_calls=[SimpleNamespace(requestor="user", id="user-call", name="user_tool")],
        ),
        SimpleNamespace(role="assistant", usage={}),
    ]
    result = audit.export(messages)
    assert [call["tool"] for call in result["tool_call_sequence"]] == [
        "get_current_time",
        "user_tool",
        "read_skill",
        "KB_search_dense",
    ]
    read = result["skill_read_positions"][0]
    assert (
        read["assistant_turn_index"],
        read["provider_request_index"],
        read["tool_call_index"],
    ) == (2, 2, 3)
    assert read["tool_calls_before"] == [1, 2]
    assert read["tool_calls_after"] == [4]
    assert read["previous_tool_call"]["tool"] == "user_tool"
    assert read["next_tool_call"]["tool"] == "KB_search_dense"
    assert read["found"] and read["repaired"]
    assert result["assistant_turns"][1]["requests"][1]["reasoning"] == [
        {"kind": "summary", "text": "Use the KB."},
    ]
    assert audit.export(messages) == result
    assert "tool_call_index" not in audit.tool_calls[0]


def test_partial_audit_keeps_failed_reads_and_missing_neighbors():
    audit = EpisodeAudit()
    turn = audit.start_turn()
    request = audit.start_request(turn, "unsupported")
    audit.record_response(turn, request, [_call("read_skill", "read")])
    audit.record_skill_read("read", "missing", {"found": False, "repaired": False})
    read = audit.export()["skill_read_positions"][0]
    assert read["previous_tool_call"] is None
    assert read["next_tool_call"] is None
    assert not read["found"]
    assert request["reasoning_status"] == "unavailable"


def test_no_calls_has_an_explicit_empty_audit():
    assert EpisodeAudit().export() == {
        "assistant_turns": [],
        "tool_call_sequence": [],
        "skill_read_positions": [],
    }
