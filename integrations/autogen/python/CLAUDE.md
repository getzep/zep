# Claude's Guide to Zep AutoGen Integration

This document provides comprehensive guidance for using and developing the Zep AutoGen integration, covering both basic usage and advanced tool development patterns.

## Overview

The Zep AutoGen integration provides two main capabilities:
1. **Memory Integration**: Persistent conversation memory using `ZepUserMemory` and `ZepGraphMemory`
2. **Tool Integration**: Exportable AutoGen tools for graph and user data operations

## The memory loop contract (read this first)

AutoGen's `Memory` interface splits the Zep loop across two independently-invoked hooks:

- `update_context()` is called **automatically** by AutoGen before every model call.
  Injection only -- it never persists anything.
- `add()` is **never called automatically**. The application must call it explicitly
  (typically once per user turn, once per assistant turn) to persist to Zep.

Because these are two separate, caller-controlled calls (not one "turn" the integration
owns), `context_builder` (see below) never runs concurrently with persistence the way it
does in the ADK / Microsoft Agent Framework / Pydantic AI ports -- there is no
`asyncio.gather` here. Don't try to add one; `update_context()` has nothing to gather the
builder against. See the README's "Zep memory loop, precisely" section for the full wiring
snippet.

## Provisioning

`zep_autogen.provisioning` exports `ensure_user`/`ensure_thread` (create-then-catch-conflict,
identical contract to the ADK/ms-agent-framework/pydantic-ai ports -- copy that module
verbatim when porting to a new framework, don't reinvent it). `ZepUserMemory` calls these
lazily from both `add()` and `update_context()`, hot-path-wrapped (log + swallow, never
raise). `ZepGraphMemory` has no lazy provisioning or `on_created` hook -- it is scoped to a
standalone `graph_id`, not a Zep user.

## Pin-or-expose tool schema: AutoGen-specific constraint

Unlike the sibling ports, `autogen_core.tools.FunctionTool` has **no raw-JSON-schema
constructor argument** -- its schema is derived strictly from the wrapped function's typed
signature via `args_base_model_from_signature`/pydantic `model_json_schema()`. So
`create_search_graph_tool` (`tools.py`) implements pin-or-expose by *dynamically building
the wrapped function's `inspect.Signature`*: parameters that should be model-visible become
real `inspect.Parameter`s (assigned to `func.__signature__`/`func.__annotations__`), while
pinned/hidden parameters are simply never parameters of the function -- they're merged in as
constants (or omitted) inside the function body before calling `graph.search`. If you touch
this code, re-verify `FunctionTool`'s introspection behavior against the installed
`autogen_core` version; don't assume the hand-crafted-JSON-schema pattern from
`zep_ms_agent_framework.search`/`zep_pydantic_ai.search` applies here.

## Memory Integration

### User Memory (Thread-based)
For conversational memory that persists across sessions:

```python
from zep_autogen import ZepUserMemory

memory = ZepUserMemory(
    client=zep_client,
    thread_id="conversation_123",
    user_id="user_456"
)

agent = AssistantAgent(
    name="Assistant",
    model_client=model_client,
    memory=[memory]
)
```

### Graph Memory
For knowledge storage and retrieval:

```python
from zep_autogen import ZepGraphMemory

memory = ZepGraphMemory(
    client=zep_client,
    graph_id="graph_123"
)

agent = AssistantAgent(
    name="Assistant", 
    model_client=model_client,
    memory=[memory]
)
```

## Tool Integration

### Creating Tool-Equipped Agents

The integration provides pre-built AutoGen tools that agents can use autonomously:

```python
from zep_autogen import create_search_graph_tool, create_add_graph_data_tool

# Create tools bound to specific resources
search_tool = create_search_graph_tool(zep_client, graph_id="my_graph")
add_tool = create_add_graph_data_tool(zep_client, user_id="user_123")

# Create agent with tools and reflection
agent = AssistantAgent(
    name="KnowledgeAssistant",
    model_client=OpenAIChatCompletionClient(model="gpt-4o-mini"), 
    tools=[search_tool, add_tool],
    system_message="You can search and add information to knowledge bases.",
    reflect_on_tool_use=True,  # Critical for natural language responses
)
```

### Key AutoGen Tool Patterns

1. **Tool Binding**: Tools are bound to specific resources (graph_id or user_id) at creation time
2. **Reflection Required**: Always use `reflect_on_tool_use=True` for natural language responses
3. **Console Streaming**: Use `Console(agent.run_stream(task))` for proper tool flow visualization

### Tool Execution Flow

With `reflect_on_tool_use=True`, AutoGen follows this pattern:
1. **Tool Call Request**: Agent decides to use a tool
2. **Tool Execution**: Tool runs and returns raw results
3. **Reflection**: Agent processes results and generates natural language response

## Development Patterns

### Project Structure

```
zep_autogen/
├── src/zep_autogen/
│   ├── __init__.py          # Exports: ZepUserMemory, ZepGraphMemory, tools, provisioning, ContextInput/Builder
│   ├── memory.py            # ZepUserMemory implementation (ContextInput/ContextBuilder/DEFAULT_CONTEXT_TEMPLATE live here)
│   ├── graph_memory.py      # ZepGraphMemory implementation
│   ├── tools.py             # AutoGen tool functions (pin-or-expose create_search_graph_tool)
│   ├── provisioning.py      # ensure_user / ensure_thread / UserSetupHook (copy verbatim from sibling ports)
│   ├── limits.py            # truncate_message_content (4096/4000) / truncate_graph_data (9900)
│   └── exceptions.py        # Custom exceptions
├── examples/
│   ├── autogen_basic.py     # Basic memory example
│   ├── autogen_graph.py     # Graph memory with ontology
│   ├── autogen_tools_search.py  # Search tool only
│   └── autogen_tools_full.py    # Search + add tools
└── tests/
    ├── test_basic.py             # Basic functionality tests
    ├── test_provisioning.py      # ensure_user/ensure_thread + lazy provisioning in add()
    ├── test_context_builder.py   # context_builder + ContextInput
    ├── test_context_template.py  # context_template override + str.replace contract
    ├── test_search.py            # pin-or-expose create_search_graph_tool
    └── test_limits.py            # truncation
```

### Memory Interface Implementation

Both memory classes implement AutoGen's `Memory` interface:

```python
async def add(self, content: MemoryContent, cancellation_token: CancellationToken | None = None) -> None:
    # Store content in Zep

async def query(self, query: str | MemoryContent, cancellation_token: CancellationToken | None = None, **kwargs) -> MemoryQueryResult:
    # Search Zep and return results

async def update_context(self, model_context: ChatCompletionContext) -> UpdateContextResult:
    # Automatically inject relevant context into conversation
```

### Tool Implementation Pattern

Simple tools (e.g. `create_add_graph_data_tool`) follow AutoGen's `FunctionTool` pattern
with a bound client instance and a normal typed signature. `create_search_graph_tool` is
the exception -- per the pin-or-expose section above, its signature is built dynamically
so exposed params become real, model-visible parameters:

```python
def create_search_graph_tool(client: AsyncZep, graph_id: str | None = None, user_id: str | None = None, *,
                              pinned_params: dict[str, Any] | None = None,
                              hidden_params: set[str] | None = None) -> FunctionTool:
    # Validate parameters, resolve exposed = all params minus pinned/hidden
    signature, annotations = _build_search_signature(exposed)

    async def zep_search(*args: Any, **kwargs: Any) -> str:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        call_args = dict(bound.arguments)
        search_kwargs: dict[str, Any] = {"query": str(call_args.pop("query", ""))[:400]}
        for param_name in _SEARCH_PARAM_SPECS:
            if param_name in pinned:
                search_kwargs[param_name] = pinned[param_name]
            elif param_name in hidden:
                continue  # omit; Zep applies its own default
            elif param_name in call_args:
                value = call_args[param_name]
                if value is not None:  # never forward explicit None on the wire
                    search_kwargs[param_name] = value
        return _format_results(await client.graph.search(**search_kwargs), ...)

    zep_search.__signature__ = signature  # what FunctionTool introspects
    return FunctionTool(zep_search, description="Search Zep memory")
```

The `if value is not None` guard matters: `apply_defaults()` materializes every exposed
param, including unset optional ones (`mmr_lambda`, `center_node_uuid`) whose spec default
is `None` -- forwarding those as explicit `None` would serialize as `"mmr_lambda": null` on
the wire instead of being omitted like the SDK's own OMIT sentinel. Match this guard
against the sibling ports' `search_kwargs` construction in
`zep_pydantic_ai.search`/`zep_ms_agent_framework.search` if you touch it.

## Example Workflows

### 1. Personal Knowledge Management

```python
# Create user-specific tools
user_search = create_search_graph_tool(zep_client, user_id="alice")
user_add = create_add_graph_data_tool(zep_client, user_id="alice")

# Agent can manage personal information
agent = AssistantAgent(
    name="PersonalAssistant",
    model_client=model_client,
    tools=[user_search, user_add],
    reflect_on_tool_use=True
)

# Natural conversations
await Console(agent.run_stream(task="Remember that I completed my Node.js certification"))
await Console(agent.run_stream(task="What do you know about my professional background?"))
```

### 2. Knowledge Base Management

```python
# Create graph-specific tools  
kb_search = create_search_graph_tool(zep_client, graph_id="company_kb")
kb_add = create_add_graph_data_tool(zep_client, graph_id="company_kb")

agent = AssistantAgent(
    name="KnowledgeManager",
    model_client=model_client,
    tools=[kb_search, kb_add],
    reflect_on_tool_use=True
)

# Agent can maintain shared knowledge
await Console(agent.run_stream(task="Add this policy update to the knowledge base"))
await Console(agent.run_stream(task="What are our current remote work policies?"))
```

## Configuration

### Environment Variables
```bash
export ZEP_API_KEY="your-zep-cloud-api-key"
```

### Graph Ontology (Optional)
Define structured entities for better knowledge organization:

```python
class ProgrammingLanguage(EntityModel):
    paradigm: EntityText = Field(description="programming paradigm")
    use_case: EntityText = Field(description="primary use cases")

await zep_client.graph.set_ontology(
    entities={"ProgrammingLanguage": ProgrammingLanguage},
    edges={}
)
```

## Development Commands

```bash
# Setup
make install            # Install dev dependencies

# Development workflow
make pre-commit        # Format, lint, type-check, test
make format           # Format code with ruff
make lint             # Check code with ruff
make type-check       # Check types with mypy
make test             # Run tests

# CI workflow  
make ci               # Strict checks without auto-fixing
```

## Best Practices

### Memory Usage
- Use `ZepUserMemory` for conversational history
- Use `ZepGraphMemory` for knowledge/facts storage
- Combine both for comprehensive agent memory

### Tool Usage
- Always bind tools to specific resources at creation time
- Use `reflect_on_tool_use=True` for natural language responses
- Use `Console(agent.run_stream())` for proper tool flow visualization
- Design tools to be atomic and focused on single operations

### Error Handling
- Tools should gracefully handle API errors and return meaningful results
- Memory classes should not fail on errors, but log and continue
- Always validate required parameters at tool creation time

### Performance
- Use appropriate limits for search operations
- Consider caching for frequently accessed data
- Use async/await patterns throughout

## Troubleshooting

### Common Issues

1. **Raw tool output instead of natural language**
   - Solution: Ensure `reflect_on_tool_use=True` is set

2. **Tool creation errors**
   - Solution: Verify either `graph_id` or `user_id` is provided (not both)

3. **Memory not persisting**
   - Solution: Check Zep client credentials and network connectivity

4. **Type checking failures**
   - Solution: Run `make type-check` and fix type annotations

### Development Setup Issues

1. **`ruff` not found error**
   - Solution: Run `make install` or `uv sync --extra dev`

2. **Import errors**
   - Solution: Install in development mode with `uv pip install -e .`

## Contributing

1. Follow the existing code patterns and type annotations
2. Add tests for new functionality in `tests/`
3. Update examples when adding new features
4. Run `make pre-commit` before submitting changes
5. Update this CLAUDE.md with new patterns or insights

## Resources

- [Zep Documentation](https://help.getzep.com)
- [AutoGen Documentation](https://microsoft.github.io/autogen/stable/)
- [AutoGen Tool Guide](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/components/tools.html)