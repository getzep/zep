# zep-ag2

Zep memory integration for [AG2](https://ag2.ai)

## Installation

```bash
pip install zep-ag2
```

## Quick Start

### System Message Injection

```python
import asyncio
import os
from autogen import AssistantAgent, UserProxyAgent, LLMConfig
from zep_cloud.client import AsyncZep
from zep_ag2 import ZepMemoryManager, register_all_tools

async def main():
    zep = AsyncZep(api_key=os.environ["ZEP_API_KEY"])

    llm_config = LLMConfig(
        {"model": "gpt-4o-mini", "api_key": os.environ["OPENAI_API_KEY"]}
    )

    assistant = AssistantAgent(
        name="assistant",
        llm_config=llm_config,
        system_message="You are a helpful assistant with long-term memory.",
    )
    user_proxy = UserProxyAgent(
        name="user",
        human_input_mode="NEVER",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in (msg.get("content") or ""),
    )

    # Enrich agent with memory context
    memory_mgr = ZepMemoryManager(zep, user_id="user123", session_id="session456")
    await memory_mgr.enrich_system_message(assistant, query="conversation topic")

    # Register memory tools (sync — AG2 calls them automatically)
    register_all_tools(assistant, user_proxy, zep, user_id="user123", session_id="session456")

    result = user_proxy.initiate_chat(
        assistant, message="What do you remember about me?"
    )

asyncio.run(main())
```

### Tool-Based Memory Access

```python
from zep_ag2 import create_search_graph_tool, create_add_graph_data_tool

# Create tools bound to a user's knowledge graph
search_tool = create_search_graph_tool(zep, user_id="user123")
add_tool = create_add_graph_data_tool(zep, user_id="user123")

# Register with AG2's decorator pattern
assistant.register_for_llm(description="Search knowledge graph")(search_tool)
user_proxy.register_for_execution()(search_tool)

assistant.register_for_llm(description="Add to knowledge graph")(add_tool)
user_proxy.register_for_execution()(add_tool)
```

## Features

- **Tool-based memory access** — Register Zep search/add as AG2 tools via `@register_for_llm`
- **System message injection** — Automatically enrich agent context with relevant memories
- **Knowledge graph** — Access Zep's knowledge graph from AG2 agents
- **Conversation memory** — Store and retrieve thread-based conversation history
- **Sync tool execution** — Tools run synchronously via a background event loop, compatible with AG2's execution model

## API Reference

### ZepMemoryManager

Manages Zep memory for AG2 agents via system message injection.

- `ZepMemoryManager(client, user_id, session_id=None)` — Initialize with Zep client
- `await enrich_system_message(agent, query=None, limit=5)` — Inject memory context
- `await get_memory_context(query=None, limit=5)` — Get formatted context string
- `await add_messages(messages)` — Store messages in Zep thread
- `await get_session_facts()` — Get extracted session facts

### ZepGraphMemoryManager

Manages Zep knowledge graph for AG2 agents.

- `ZepGraphMemoryManager(client, graph_id)` — Initialize with graph ID
- `await search(query, limit=5, scope="edges")` — Search the graph
- `await add_data(data, data_type="text")` — Add data to the graph
- `await enrich_system_message(agent, query=None, limit=5)` — Inject graph context

### Tool Factories

All tool factories return **synchronous** callables (AG2 executes tools synchronously).
Internally they use a background event loop to call the async Zep SDK.

- `create_search_memory_tool(client, user_id, session_id=None)` — Search conversation memory
- `create_add_memory_tool(client, user_id, session_id=None)` — Add conversation memory
- `create_search_graph_tool(client, user_id=None, graph_id=None)` — Search knowledge graph
- `create_add_graph_data_tool(client, user_id=None, graph_id=None)` — Add graph data
- `register_all_tools(agent, executor, client, user_id, ...)` — Register all tools at once

## Examples

- **[Basic Memory](examples/ag2_basic.py)** — System message injection + memory tools
- **[Graph Memory](examples/ag2_graph.py)** — Knowledge graph with ZepGraphMemoryManager
- **[Search Tools](examples/ag2_tools_search.py)** — Read-only search tool registration
- **[Full Tools](examples/ag2_tools_full.py)** — All tools in a GroupChat with multiple agents
- **[Manual Test](examples/manual_test.py)** — End-to-end integration test with real APIs

## Configuration

### Environment Variables

```bash
# Required
export ZEP_API_KEY="your-zep-cloud-api-key"

# Required for examples that use LLM
export OPENAI_API_KEY="your-openai-api-key"
```

## Development

```bash
make install        # Install dev dependencies
make pre-commit     # Format, lint, type-check, test
make ci             # CI validation
```

## Requirements

- Python 3.10+
- `ag2>=0.9.0`
- `zep-cloud>=3.3.0`

## License

Apache-2.0 — see [LICENSE](../../../LICENSE) for details.

## Support

- [Zep Documentation](https://help.getzep.com)
- [AG2 Documentation](https://docs.ag2.ai)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.
