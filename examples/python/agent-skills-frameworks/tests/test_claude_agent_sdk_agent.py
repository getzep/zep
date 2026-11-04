import asyncio

from banking import make_case
from claude_agent_sdk_agent import TraceHooks, build_tools, record_assistant_text
from tools import BankingTools


class RecordingTrace:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str | None, str | None]] = []

    def append(
        self,
        event_type: str,
        content: str,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        self.events.append((event_type, content, tool_name, tool_call_id))


def test_claude_hooks_and_mcp_tool_expose_tool_error() -> None:
    trace = RecordingTrace()
    hooks = TraceHooks(trace)  # type: ignore[arg-type]
    mcp_tools = build_tools(BankingTools(make_case(10134), []))
    read_document = next(item for item in mcp_tools if item.name == "read_document")

    result = asyncio.run(read_document.handler({"document_id": "missing"}))
    assert result == {
        "content": [{"type": "text", "text": "The document ID is not in list_documents."}],
        "is_error": True,
    }
    asyncio.run(
        hooks.pre_tool_use(
            {
                "tool_name": "mcp__banking__read_document",
                "tool_input": {"document_id": "missing"},
                "tool_use_id": "call-1",
            }
        )
    )
    asyncio.run(
        hooks.post_tool_use_failure(
            {
                "tool_name": "mcp__banking__read_document",
                "error": "The document ID is not in list_documents.",
                "tool_use_id": "call-1",
            }
        )
    )
    record_assistant_text(trace, "I could not read that document.")  # type: ignore[arg-type]

    assert [event[0] for event in trace.events] == [
        "tool_call",
        "tool_result",
        "decision",
    ]
    assert trace.events[0] == (
        "tool_call",
        '{"document_id": "missing"}',
        "read_document",
        "call-1",
    )
    assert trace.events[1][1] == "The document ID is not in list_documents."
    assert trace.events[1][2:] == ("read_document", "call-1")
