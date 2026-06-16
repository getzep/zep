# Zep CrewAI Integration

A comprehensive integration package that enables [CrewAI](https://github.com/joaomdmoura/crewai) agents to leverage [Zep](https://getzep.com)'s powerful memory platform for persistent storage, knowledge graphs, and intelligent tool usage.

## Installation

```bash
pip install zep-crewai
```

## Quick Start

### User Storage with Conversation Memory

> **CrewAI 1.x note:** CrewAI 1.x removed `crewai.memory.storage.interface.Storage`
> and the `ExternalMemory(storage=...)` wrapper (and the `external_memory=` Crew
> kwarg). The `ZepUserStorage`, `ZepGraphStorage`, and `ZepStorage` classes are now
> standalone, framework-agnostic adapters with the same `save` / `search` / `reset`
> API. Persist context with `storage.save(...)` and expose Zep to agents through the
> `ZepSearchTool` / `ZepAddDataTool` (the supported CrewAI extension point).

```python
import os
from zep_cloud.client import Zep
from zep_crewai import ZepUserStorage, create_search_tool
from crewai import Agent, Crew, Task

# Initialize Zep client
zep_client = Zep(api_key=os.getenv("ZEP_API_KEY"))

# Create user and thread
zep_client.user.add(user_id="alice_123", first_name="Alice")
zep_client.thread.create(user_id="alice_123", thread_id="project_456")

# Create user storage
user_storage = ZepUserStorage(
    client=zep_client,
    user_id="alice_123",
    thread_id="project_456",  # for conversation context
)

# Persist conversation turns and business data
user_storage.save("How can I help?", metadata={"type": "message", "role": "assistant"})

# Give an agent a Zep search tool so it can retrieve context on demand
agent = Agent(
    role="Personal Assistant",
    tools=[create_search_tool(zep_client, user_id="alice_123")],
)

crew = Crew(agents=[agent], tasks=[...])
```

### Knowledge Graph Storage

```python
from zep_crewai import ZepGraphStorage, create_search_tool

# Create graph storage for shared knowledge
graph_storage = ZepGraphStorage(
    client=zep_client,
    graph_id="company_knowledge",
    search_filters={"node_labels": ["Technology", "Project"]}
)

# Persist knowledge, then let agents search it through a tool
graph_storage.save("Project Alpha uses Python and React", metadata={"type": "text"})

agent = Agent(
    role="Knowledge Assistant",
    tools=[create_search_tool(zep_client, graph_id="company_knowledge")],
)

crew = Crew(agents=[agent], tasks=[...])
```

### Tool-Equipped Agents

```python
from zep_crewai import create_search_tool, create_add_data_tool

# Create tools for user or graph
search_tool = create_search_tool(zep_client, user_id="alice_123")
add_tool = create_add_data_tool(zep_client, graph_id="knowledge_base")

# Create agent with Zep tools
agent = Agent(
    role="Knowledge Assistant",
    goal="Manage and retrieve information efficiently",
    tools=[search_tool, add_tool],
    llm="gpt-4o-mini"
)
```

## Features

### Storage Classes

#### ZepUserStorage
Manages user-specific memories and conversations:
- **Thread Messages**: Conversation history with role-based storage
- **User Graph**: Personal knowledge, preferences, and context
- **Parallel Search**: Simultaneous search across threads and graphs
- **Search Filters**: Target specific node types and relationships
- **Thread Context**: Uses `thread.get_user_context` to return Zep's auto-assembled Context Block

#### ZepGraphStorage  
Manages generic knowledge graphs for shared information:
- **Structured Knowledge**: Store entities with defined ontologies
- **Multi-scope Search**: Search edges (facts), nodes (entities), and episodes
- **Search Filters**: Filter by node labels and attributes
- **Persistent Storage**: Knowledge persists across sessions
- **Context Composition**: Uses `compose_context_string` for formatted context

### Tool Integration

#### Search Tool
```python
search_tool = create_search_tool(
    zep_client,
    user_id="user_123"  # OR graph_id="knowledge_base"
)
```
- Search across edges, nodes, and episodes
- Configurable result limits
- Scope filtering (edges, nodes, episodes, or all)
- Natural language queries

#### Add Data Tool
```python
add_tool = create_add_data_tool(
    zep_client,
    graph_id="knowledge_base"  # OR user_id="user_123"
)
```
- Add text, JSON, or message data
- Automatic type detection
- Structured data support
- Metadata preservation

## Advanced Usage

### Graph Storage with Ontology

Define structured entities for better organization:

```python
from zep_cloud.external_clients.ontology import EntityModel, EntityText
from pydantic import Field

class ProjectEntity(EntityModel):
    status: EntityText = Field(description="project status")
    priority: EntityText = Field(description="priority level")
    team_size: EntityText = Field(description="team size")

# Set ontology
zep_client.graph.set_ontology(
    graph_id="projects",
    entities={"Project": ProjectEntity},
    edges={}
)

# Use with filtered search and context limits
graph_storage = ZepGraphStorage(
    client=zep_client,
    graph_id="projects",
    search_filters={"node_labels": ["Project"]},
    facts_limit=20,  # Max facts for context
    entity_limit=5   # Max entities for context
)

# Search the graph (returns a list with a composed context string)
results = graph_storage.search("project status")
print(results)  # [{"context": "...facts and entities...", ...}]
```

### Multi-Agent with Mixed Storage

```python
# User-specific storage for personal agent
personal_storage = ZepUserStorage(
    client=zep_client,
    user_id="user_123",
    thread_id="thread_456",
    facts_limit=20,  # Max facts for context
    entity_limit=5,  # Max entities for context
)

# Get the Context Block for the thread (auto-assembled by Zep)
context = personal_storage.get_context()
print(context)  # Prompt-ready Context Block string

# Shared knowledge graph for team agent
team_storage = ZepGraphStorage(
    client=zep_client,
    graph_id="team_knowledge"
)

# Create agents with different storage
personal_agent = Agent(
    name="Personal Assistant",
    tools=[create_search_tool(zep_client, user_id="user_123")]
)

team_agent = Agent(
    name="Team Coordinator",
    tools=[create_search_tool(zep_client, graph_id="team_knowledge")]
)
```

### Storage Routing

Different data types are automatically routed:

```python
# Messages go to thread (if thread_id is set)
user_storage.save(
    "How can I help you today?",
    metadata={"type": "message", "role": "assistant", "name": "Helper"}
)

# JSON data goes to graph
user_storage.save(
    '{"project": "Alpha", "status": "active", "budget": 50000}',
    metadata={"type": "json"}
)

# Text data goes to graph
user_storage.save(
    "Project Alpha requires Python and React expertise",
    metadata={"type": "text"}
)
```

## Examples

### Complete Examples

- **[User Storage](examples/crewai_user.py)**: Personal assistant with conversation memory
- **[Graph Storage](examples/crewai_graph.py)**: Knowledge graph with ontology
- **[Tools Usage](examples/crewai_tools.py)**: Agents using search and add tools
- **[Simple Example](examples/simple_example.py)**: Basic setup and usage

### Common Patterns

#### Personal Assistant
```python
# Store user preferences and context
user_storage = ZepUserStorage(client=zep_client, user_id="user_123", thread_id="thread_456")
user_storage.save("User prefers morning meetings", metadata={"type": "text"})

# Agent retrieves relevant context via a Zep search tool
personal_assistant = Agent(
    role="Personal Assistant",
    tools=[create_search_tool(zep_client, user_id="user_123")],
    backstory="You know the user's preferences and history"
)
```

#### Knowledge Base Management
```python
# Shared knowledge with search tools
knowledge_tools = [
    create_search_tool(zep_client, graph_id="knowledge"),
    create_add_data_tool(zep_client, graph_id="knowledge")
]

curator = Agent(
    role="Knowledge Curator",
    tools=knowledge_tools,
    backstory="You maintain the organization's knowledge base"
)
```

#### Multi-Modal Memory
```python
# Combine user and graph storage with tools
research_agent = Agent(
    role="Research Analyst",
    tools=[
        create_search_tool(zep_client, user_id="user_123"),
        create_search_tool(zep_client, graph_id="research_data")
    ],
    backstory="You analyze both personal and organizational data"
)
```

## Configuration

### Environment Variables

```bash
# Required: Your Zep Cloud API key
export ZEP_API_KEY="your-zep-api-key"
```

### Storage Parameters

#### ZepUserStorage
- `client`: Zep client instance (required)
- `user_id`: User identifier (required)
- `thread_id`: Thread identifier (optional)
- `search_filters`: Search filters (optional)
- `facts_limit`: Maximum facts for context (default: 20)
- `entity_limit`: Maximum entities for context (default: 5)
- `mode`: Deprecated and ignored (Zep V3 removed the thread context mode option)

#### ZepGraphStorage
- `client`: Zep client instance (required)
- `graph_id`: Graph identifier (required)
- `search_filters`: Search filters (optional)
- `facts_limit`: Maximum facts for context (default: 20)
- `entity_limit`: Maximum entities for context (default: 5)

### Tool Parameters

#### Search Tool
- `query`: Search query string
- `limit`: Maximum results (default: 10)
- `scope`: Search scope - "edges", "nodes", "episodes", or "all"

#### Add Data Tool
- `data`: Content to store
- `data_type`: Type - "text", "json", or "message"

## Development

### Setup
```bash
# Clone the repository
git clone https://github.com/getzep/zep.git
cd integrations/crewai/python

# Install dependencies
pip install -e .
pip install -r requirements-dev.txt
```

### Testing
```bash
# Run tests
pytest tests/

# Run with coverage
pytest --cov=zep_crewai tests/
```

### Type Checking
```bash
mypy src/zep_crewai
```

## Requirements

- Python 3.10+
- `zep-cloud>=3.23.0`
- `crewai>=1.0.0`
- `pydantic>=2.0.0`

## Best Practices

1. **Storage Selection**
   - Use `ZepUserStorage` for user-specific, personal data
   - Use `ZepGraphStorage` for shared, organizational knowledge

2. **Tool Usage**
   - Bind tools to specific users or graphs at creation
   - Use search scope "all" sparingly (more expensive)
   - Add data with appropriate types for better organization

3. **Memory Management**
   - Set up ontologies for structured data
   - Use search filters to improve relevance
   - Combine storage types for comprehensive memory

4. **Performance**
   - Allow 10-20 seconds for data processing after additions
   - Use parallel search for better performance
   - Limit search results appropriately

## Support

- [Zep Documentation](https://help.getzep.com)
- [CrewAI Documentation](https://docs.crewai.com)
- [GitHub Issues](https://github.com/getzep/zep/issues)

## License

Apache 2.0 - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../../../CONTRIBUTING.md) for details.