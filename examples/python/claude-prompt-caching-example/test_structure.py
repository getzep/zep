"""Offline structural test: simulates turns in both modes with a stub LLM and
verifies request shapes, Opus 4.8 system-message placement rules, explicit
cache-breakpoint placement, prefix stability (the thing caching depends on),
and history bookkeeping.

Run: python test_structure.py  (no API keys needed)
"""

import copy
from unittest.mock import MagicMock

import json

from agent import MODE_SYSTEM_MESSAGE, MODE_SYSTEM_PROMPT, ReplayMemory, ZepMemoryAgent
from scenario import CONVERSATIONS, PRIOR_CONVERSATIONS, STATIC_SYSTEM_PROMPT, TOOL_DEFINITIONS

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
    """Opus 4.8 rules: a system message is never first, must immediately
    follow a user turn, must be last or followed by an assistant turn, and
    never consecutive."""
    for i, msg in enumerate(messages):
        if msg["role"] != "system":
            continue
        assert i > 0, "system message is first"
        assert messages[i - 1]["role"] == "user", f"system msg at {i} not after a user turn"
        assert i == len(messages) - 1 or messages[i + 1]["role"] == "assistant", (
            f"system msg at {i} neither last nor followed by assistant"
        )


def validate_breakpoints(mode, system_blocks, messages):
    """system-prompt mode caches only the static prefix: one breakpoint on
    the first (static) system block, none on the changing context block,
    none in the messages (they could never match). system-message mode
    places exactly two explicit breakpoints: end of system field + latest
    user message — never on a trailing system (context) message."""
    if mode == MODE_SYSTEM_PROMPT:
        assert "cache_control" in system_blocks[0], "static system block should carry a breakpoint"
        assert all("cache_control" not in b for b in system_blocks[1:]), "changing context block must not carry a breakpoint"
        for msg in messages:
            assert all("cache_control" not in b for b in msg["content"]), "message breakpoints would never match in this mode"
        return
    assert "cache_control" in system_blocks[-1], "system field should end with a cache breakpoint"
    last_user_idx = max(i for i, m in enumerate(messages) if m["role"] == "user")
    assert "cache_control" in messages[last_user_idx]["content"][-1], "latest user message should carry the moving breakpoint"
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
        assert kw["model"] == "claude-opus-4-8"
        assert kw["tools"] == TOOL_DEFINITIONS, "tool definitions should be in every request"
        assert kw["tool_choice"] == {"type": "none"}, "tool_choice none should accompany the tools"
        sys_blocks, msgs = kw["system"], kw["messages"]
        assert sys_blocks[0]["text"].startswith(STATIC_SYSTEM_PROMPT[:50])
        assert "Operations manual" in sys_blocks[0]["text"], "full production prefix should be used"
        assert "testsalt" in sys_blocks[0]["text"]
        validate_placement(msgs)
        validate_breakpoints(mode, sys_blocks, msgs)
        if mode == MODE_SYSTEM_PROMPT:
            assert len(sys_blocks) == 2 and f"fact set {call_i}" in sys_blocks[1]["text"], (
                "baseline: context rides in a second, changing system block"
            )
            assert all(m_["role"] != "system" for m_ in msgs), "no system msgs expected in messages"
        else:
            assert len(sys_blocks) == 1 and "fact set" not in sys_blocks[0]["text"], "static system only"
            assert msgs[-1]["role"] == "system" and f"fact set {call_i}" in text_of(msgs[-1])
            assert sum(1 for m_ in msgs if m_["role"] == "system") == 1, (
                "exactly one (fresh) context message per request — old ones are replaced, not accumulated"
            )

    if mode == MODE_SYSTEM_MESSAGE:
        # Cache-preserving invariants (modulo cache_control markers, which
        # move forward by design and are excluded from prefix hashing):
        # system field byte-identical across turns, and each request's
        # messages — minus its trailing, never-cached context message — an
        # exact prefix of the next request's messages.
        for a, b in zip(captured, captured[1:]):
            assert strip_cache_control(b["system"]) == strip_cache_control(a["system"]), (
                "system field changed between turns"
            )
            a_msgs, b_msgs = strip_cache_control(a["messages"]), strip_cache_control(b["messages"])
            a_cached = a_msgs[:-1] if a_msgs[-1]["role"] == "system" else a_msgs
            assert b_msgs[: len(a_cached)] == a_cached, "cached prefix not preserved across turns"
            assert a_msgs[-1] not in b_msgs, "the prior context message must be replaced, not carried forward"
    else:
        for a, b in zip(captured, captured[1:]):
            assert strip_cache_control(b["system"][0]) == strip_cache_control(a["system"][0]), (
                "the static system block must stay byte-identical (it's the cached part)"
            )
            assert strip_cache_control(b["system"][1]) != strip_cache_control(a["system"][1]), (
                "the context system block should change every turn in the baseline"
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
