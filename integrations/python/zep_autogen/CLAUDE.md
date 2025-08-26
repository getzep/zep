# Claude's Guide to Zep AutoGen Integration

This document provides comprehensive guidance for using and developing the Zep AutoGen integration, covering both basic usage and advanced tool development patterns.

## Overview

The Zep AutoGen integration provides two main capabilities:
1. **Memory Integration**: Persistent conversation memory using `ZepUserMemory` and `ZepGraphMemory`
2. **Tool Integration**: Exportable AutoGen tools for graph and user data operations

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
│   ├── __init__.py          # Exports: ZepUserMemory, ZepGraphMemory, tools
│   ├── memory.py            # ZepUserMemory implementation
│   ├── graph_memory.py      # ZepGraphMemory implementation
│   ├── tools.py             # AutoGen tool functions
│   └── exceptions.py        # Custom exceptions
├── examples/
│   ├── autogen_basic.py     # Basic memory example
│   ├── autogen_graph.py     # Graph memory with ontology
│   ├── autogen_tools_search.py  # Search tool only
│   └── autogen_tools_full.py    # Search + add tools
└── tests/
    └── test_basic.py        # Basic functionality tests
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

Tools follow AutoGen's `FunctionTool` pattern with bound client instances:

```python
def create_search_graph_tool(client: AsyncZep, graph_id: str = None, user_id: str = None) -> FunctionTool:
    # Validate parameters
    if not graph_id and not user_id:
        raise ValueError("Either graph_id or user_id must be provided")
    
    # Create bound function
    async def bound_search_memory(
        query: Annotated[str, "Search query"],
        limit: Annotated[int, "Max results"] = 10,
    ) -> List[Dict[str, Any]]:
        return await search_memory(client, query, graph_id, user_id, limit)
    
    return FunctionTool(bound_search_memory, description="Search Zep memory")
```

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