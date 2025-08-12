# Zep AutoGen Integration

A comprehensive integration package that enables [Zep](https://getzep.com) to work seamlessly with [Microsoft AutoGen](https://github.com/microsoft/autogen) agents, providing persistent conversation memory, knowledge graphs, and intelligent tool usage.

## Installation

```bash
pip install zep-autogen
```

## Quick Start

### Basic Memory Integration

```python
import asyncio
from zep_cloud.client import AsyncZep
from zep_autogen import ZepUserMemory
from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient


async def main():
    # Initialize Zep client
    zep_client = AsyncZep(api_key="your-zep-api-key")

    # Create Zep memory for your agent
    memory = ZepUserMemory(
        client=zep_client,
        user_id="user_123",
        thread_id="conversation_456"
    )

    # Create AutoGen agent with Zep memory
    agent = AssistantAgent(
        name="MemoryAwareAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4.1-mini"),
        memory=[memory]  # Add Zep memory to the agent
    )

    # Your agent now has persistent memory across conversations!
    response = await agent.run(task="What's my name again?")
    print(response.messages[-1].content)


asyncio.run(main())
```

### Tool-Equipped Agents

```python
import asyncio
from zep_cloud.client import AsyncZep
from zep_autogen import create_search_graph_tool, create_add_graph_data_tool
from autogen_agentchat.agents import AssistantAgent
from autogen_agentchat.ui import Console
from autogen_ext.models.openai import OpenAIChatCompletionClient


async def main():
    # Initialize Zep client
    zep_client = AsyncZep(api_key="your-zep-api-key")
    
    # Create tools bound to your graph
    search_tool = create_search_graph_tool(zep_client, graph_id="my_knowledge_base")
    add_tool = create_add_graph_data_tool(zep_client, graph_id="my_knowledge_base")

    # Create agent with tools and reflection
    agent = AssistantAgent(
        name="KnowledgeAssistant",
        model_client=OpenAIChatCompletionClient(model="gpt-4o-mini"),
        tools=[search_tool, add_tool],
        system_message="You can search and add information to knowledge bases.",
        reflect_on_tool_use=True,  # Enables natural language responses
    )

    # Agent can autonomously use tools and provide natural responses
    await Console(agent.run_stream(task="Add this fact: Python is great for AI development"))
    await Console(agent.run_stream(task="What do you know about Python?"))


asyncio.run(main())
```

## Features

- **Persistent Memory**: Conversations and knowledge persist across agent sessions
- **Graph Memory**: Store and retrieve structured knowledge using Zep's graph capabilities  
- **Tool Integration**: Pre-built AutoGen tools for search and data operations
- **Intelligent Retrieval**: Zep's memory automatically provides relevant context
- **AutoGen Compatible**: Seamlessly integrates with AutoGen's memory and tool interfaces
- **Natural Language Tool Use**: Agents process tool results and provide conversational responses
- **Async Support**: Full async/await support for modern applications
- **Type Safety**: Fully typed with comprehensive type hints

## Configuration

### Environment Variables

```bash
# Required: Your Zep Cloud API key
export ZEP_API_KEY="your-zep-api-key"
```

### Memory Classes

#### ZepUserMemory
For conversational memory that persists across threads:

- `client` (AsyncZep): Your Zep client instance
- `user_id` (str): Unique identifier for the user
- `thread_id` (str, optional): Thread/conversation identifier

#### ZepGraphMemory  
For knowledge graph storage and retrieval:

- `client` (AsyncZep): Your Zep client instance
- `graph_id` (str): Identifier for the knowledge graph

### Tool Functions

#### create_search_graph_tool
Creates a search tool bound to a graph or user:

- `client` (AsyncZep): Your Zep client instance  
- `graph_id` (str, optional): Graph to search (for general knowledge graphs)
- `user_id` (str, optional): User to search (for user knowledge graphs)

#### create_add_graph_data_tool
Creates a data addition tool bound to a graph or user:

- `client` (AsyncZep): Your Zep client instance
- `graph_id` (str, optional): Graph to add data to
- `user_id` (str, optional): User to add data for

## Examples

### Memory Integration

- **[Basic Memory](examples/autogen_basic.py)**: User memory with conversation persistence
- **[Graph Memory](examples/autogen_graph.py)**: Knowledge graphs with ontology definitions

### Tool Integration

- **[Search Tools](examples/autogen_tools_search.py)**: Agents with search-only capabilities  
- **[Full Tools](examples/autogen_tools_full.py)**: Agents that can both search and add data

### Multi-Agent with Shared Memory

```python
# Multiple agents can share the same memory context
shared_memory = ZepUserMemory(
    client=zep_client,
    user_id="team_project", 
    thread_id="brainstorm_session"
)

researcher = AssistantAgent(
    name="Researcher",
    model_client=model_client,
    memory=[shared_memory]
)

writer = AssistantAgent(
    name="Writer",
    model_client=model_client, 
    memory=[shared_memory]
)
```

## Advanced Usage

### Graph Memory with Ontology

Define structured entities for better knowledge organization:

```python
from zep_cloud.external_clients.ontology import EntityModel, EntityText
from pydantic import Field

class ProgrammingLanguage(EntityModel):
    paradigm: EntityText = Field(description="programming paradigm")  
    use_case: EntityText = Field(description="primary use cases")

# Set graph ontology
await zep_client.graph.set_ontology(
    entities={"ProgrammingLanguage": ProgrammingLanguage},
    edges={}
)

# Use graph memory with ontology
memory = ZepGraphMemory(
    client=zep_client,
    graph_id="tech_knowledge",
    search_filters=SearchFilters(
        node_labels=["ProgrammingLanguage"],
    ),
)
```

### Tool Usage
```python

agent = AssistantAgent(
    name="Assistant",
    model_client=model_client,
    tools=[search_tool],
    reflect_on_tool_use=True, 
)

# Use streaming console for tool visualization
await Console(agent.run_stream(task="Search for Python information"))
```

## Development

### Setup
```bash
# Install development dependencies
make install

# Run pre-commit checks (format, lint, type-check, test)
make pre-commit
```

### Available Commands
- `make format` - Format code with ruff
- `make lint` - Run linting checks
- `make type-check` - Run type checking with mypy  
- `make test` - Run tests
- `make all` - Run all checks

## Requirements

- Python 3.10+
- `zep-cloud>=3.2.0`
- `autogen-agentchat>=0.6.1`
- `autogen-ext[azure,openai]>=0.6.1`

## License

MIT License - see [LICENSE](LICENSE) for details.

## Support

- [Zep Documentation](https://help.getzep.com)
- [AutoGen Documentation](https://microsoft.github.io/autogen/stable/)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.