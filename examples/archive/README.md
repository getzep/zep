# Archived examples

The examples in this directory show the older memory-layer pattern. In this
pattern, the application adds the chat history to Zep. On each turn, the
application puts the context that Zep returns into the prompt.

These examples are not maintained. They can fail with a later SDK release.

Do not use these examples for new code. New code gives the agent context
retrieval as tools, for example search and `get_context`. Use the tool-based
reference agents:

- [`agent-with-zep`](../python/agent-with-zep) uses Pydantic AI.
- [`agent-with-zep-adk`](../python/agent-with-zep-adk) uses Google ADK.

## Contents

| Archived example | Earlier location |
| --- | --- |
| [`python/agent-memory-full-example`](./python/agent-memory-full-example) | `examples/python/agent-memory-full-example` |
| [`python/chat_history`](./python/chat_history) | `examples/python/chat_history` |
| [`python/claude-prompt-caching-example`](./python/claude-prompt-caching-example) | `examples/python/claude-prompt-caching-example` |
| [`python/simple`](./python/simple) | `examples/python/simple.py` |
| [`typescript/memory`](./typescript/memory) | `examples/typescript/memory` |
