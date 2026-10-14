"""Offline structural test: simulates turns in both modes with a stub LLM and
verifies request shapes, user-data context placement, explicit
cache-breakpoint placement, prefix stability (the thing caching depends on),
and history bookkeeping.

Run: python test_structure.py  (no API keys needed)
"""

import copy
import json
from itertools import pairwise
from unittest.mock import MagicMock

from agent import MODE_SYSTEM_MESSAGE, MODE_SYSTEM_PROMPT, ReplayMemory, ZepMemoryAgent
from scenario import (
    CONVERSATIONS,
    PRIOR_CONVERSATIONS,
    STATIC_SYSTEM_PROMPT,
    TOOL_DEFINITIONS,
)

turns = CONVERSATIONS["short"][:3]
contexts = [f"<FACTS>fact set {i}</FACTS>" for i in range(3)]

captured = []


class FakeStream:
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        captured.append(copy.deepcopy(self.kwargs))
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        yield "stub reply"

    def get_final_message(self):
        m = MagicMock()
        m.usage.input_tokens = 100
        m.usage.cache_creation_input_tokens = 50
        m.usage.cache_read_input_tokens = 200
        m.usage.output_tokens = 10
        return m


client = MagicMock()
client.messages.stream.side_effect = lambda **kw: FakeStream(kw)


def text_of(msg) -> str:
    content = msg["content"]
    if isinstance(content, str):
        return content
    return "".join(b["text"] for b in content if b.get("type") == "text")


def strip_cache_control(obj):
    """Remove cache_control markers — they move between requests by design
    and are excluded from the API's prefix hashing."""
    if isinstance(obj, list):
        return [strip_cache_control(x) for x in obj]
    if isinstance(obj, dict):
        return {k: strip_cache_control(v) for k, v in obj.items() if k != "cache_control"}
    return obj


def validate_placement(messages):
    """Retrieved context must stay in user messages, not system messages."""
    assert all(msg["role"] != "system" for msg in messages)


def validate_breakpoints(mode, system_blocks, messages):
    """Check cache breakpoints for each user-data context placement."""
    if mode == MODE_SYSTEM_PROMPT:
        assert "cache_control" in system_blocks[0], "static system block should carry a breakpoint"
        assert len(system_blocks) == 1, "system prompt should stay static"
        for msg in messages:
            assert all("cache_control" not in b for b in msg["content"]), "message breakpoints would never match in this mode"
        return
    assert "cache_control" in system_blocks[-1], "system field should end with a cache breakpoint"
    breakpoint_indices = [
        i for i, msg in enumerate(messages)
        if any("cache_control" in block for block in msg["content"])
    ]
    assert len(breakpoint_indices) == 1, "latest user turn should carry one moving breakpoint"
    last_user_idx = breakpoint_indices[0]
    assert messages[last_user_idx]["role"] == "user", "user data should carry the moving breakpoint"
    for i, msg in enumerate(messages):
        for block in msg["content"]:
            if "cache_control" in block:
                assert i == last_user_idx, f"unexpected breakpoint on message {i} ({msg['role']})"


for mode in (MODE_SYSTEM_PROMPT, MODE_SYSTEM_MESSAGE):
    captured.clear()
    agent = ZepMemoryAgent(client, ReplayMemory(contexts), mode, system_salt="testsalt")
    for t in turns:
        reply, m = agent.send(t["user"], scripted_reply=t["assistant"])
        assert reply == "stub reply" and m.cache_read_input_tokens == 200

    for call_i, kw in enumerate(captured):
        assert kw["model"] == "claude-opus-5"
        assert kw["tools"] == TOOL_DEFINITIONS, "tool definitions should be in every request"
        assert kw["tool_choice"] == {"type": "none"}, "tool_choice none should accompany the tools"
        sys_blocks, msgs = kw["system"], kw["messages"]
        assert sys_blocks[0]["text"].startswith(STATIC_SYSTEM_PROMPT[:50])
        assert "Operations manual" in sys_blocks[0]["text"], "full production prefix should be used"
        assert "testsalt" in sys_blocks[0]["text"]
        validate_placement(msgs)
        validate_breakpoints(mode, sys_blocks, msgs)
        if mode == MODE_SYSTEM_PROMPT:
            assert len(sys_blocks) == 1 and "fact set" not in sys_blocks[0]["text"], "system prompt must stay static"
            assert f"fact set {call_i}" in text_of(msgs[-1]), "context must be in the user turn"
        else:
            assert len(sys_blocks) == 1 and "fact set" not in sys_blocks[0]["text"], "static system only"
            assert msgs[-1]["role"] == "user" and f"fact set {call_i}" in text_of(msgs[-1])
            assert sum(1 for m_ in msgs if "fact set" in text_of(m_)) == 1, (
                "exactly one (fresh) context message per request — old ones are replaced, not accumulated"
            )

    if mode == MODE_SYSTEM_MESSAGE:
        # Cache-preserving invariants (modulo cache_control markers, which
        # move forward by design and are excluded from prefix hashing):
        # system field byte-identical across turns, and each request's
        # messages — minus its trailing, never-cached context data — an
        # exact prefix of the next request's messages.
        for a, b in pairwise(captured):
            assert strip_cache_control(b["system"]) == strip_cache_control(a["system"]), (
                "system field changed between turns"
            )
            a_msgs, b_msgs = strip_cache_control(a["messages"]), strip_cache_control(b["messages"])
            a_cached = a_msgs[:-1]
            assert b_msgs[: len(a_cached)] == a_cached, "cached prefix not preserved across turns"
            assert a_msgs[-1] not in b_msgs, "the prior context data must be replaced, not carried forward"
    else:
        for a, b in pairwise(captured):
            assert strip_cache_control(b["system"]) == strip_cache_control(a["system"]), (
                "the static system prompt must stay byte-identical"
            )

    assert text_of(agent.history[-1]) == turns[-1]["assistant"], "scripted reply should enter history"
    assert all(m_["role"] != "system" for m_ in agent.history), "context blocks never enter history"
    print(f"{mode}: OK ({len(captured)} calls validated)")

for i, conv in enumerate(PRIOR_CONVERSATIONS, start=1):
    assert len(conv) == 10, f"prior conversation {i} should have 10 messages, has {len(conv)}"

prefix_chars = len(STATIC_SYSTEM_PROMPT) + len(json.dumps(TOOL_DEFINITIONS))
print(f"static prefix: {prefix_chars:,} chars ({len(TOOL_DEFINITIONS)} tools)")
print(
    f"prior conversations: {len(PRIOR_CONVERSATIONS)} x 10 messages | "
    f"long conversation: {len(CONVERSATIONS['long'])} turns | short: {len(CONVERSATIONS['short'])} turns"
)
print("All structural checks passed.")
