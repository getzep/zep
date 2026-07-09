# Zep CrewAI Integration

A comprehensive integration package that enables [CrewAI](https://github.com/joaomdmoura/crewai) agents to leverage [Zep](https://getzep.com)'s powerful memory platform for persistent storage, knowledge graphs, and intelligent tool usage.

## Installation

```bash
pip install zep-crewai
```

## CrewAI 1.x framework ceiling — no automatic memory loop

CrewAI 1.x **removed** `crewai.memory.storage.interface.Storage` and the
`ExternalMemory(storage=...)` wrapper (and the `external_memory=` Crew kwarg), so **no
automatic per-turn memory loop is possible** with this framework version — there is no
seam where an integration can transparently persist each turn and inject context before
each model call. This package is also **sync-only**: CrewAI's adapters are built on the
synchronous `Zep` client, so all APIs here are synchronous. The supported extension
points are:

1. **Tools** — give agents a `ZepSearchTool` / `ZepAddDataTool` so the model decides
   when to read from or write to Zep (the primary CrewAI 1.x extension point).
2. **Storage adapters called from your app code** — `ZepUserStorage`,
   `ZepGraphStorage`, and `ZepStorage` are standalone, framework-agnostic adapters with
   the historical `save` / `search` / `reset` API. Your application calls
   `storage.save(...)` after turns and `storage.search(...)` / `storage.get_context()`
   before kickoff.
3. **Kickoff-level seeding** — retrieve a Zep Context Block (e.g.
   `user_storage.get_context()`) and interpolate it into task descriptions or agent
   backstories before `crew.kickoff()`.

Re-check this on future CrewAI releases: if CrewAI reintroduces a memory extension
point, this integration should adopt it.

## Quick Start

### User Storage with Conversation Memory

```python
import os
from zep_cloud.client import Zep
from zep_crewai import ZepUserStorage, create_search_tool, ensure_user, ensure_thread
from crewai import Agent, Crew, Task

# Initialize Zep client
zep_client = Zep(api_key=os.getenv("ZEP_API_KEY"))

# Provision the user and thread out-of-band (idempotent; genuine failures raise)
ensure_user(zep_client, user_id="alice_123", first_name="Alice", email="alice@example.com")
ensure_thread(zep_client, thread_id="project_456", user_id="alice_123")

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
    llm="gpt-5-mini"
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

#### Search Tool (pin-or-expose)

Every `graph.search` parameter — `scope` (`edges`, `nodes`, `episodes`, `observations`,
`thread_summaries`, `auto`), `reranker` (`rrf`, `mmr`, `node_distance`,
`episode_mentions`, `cross_encoder`), `limit`, `mmr_lambda`, `center_node_uuid` — is
exposed to the model in the tool's schema by default. Use `pinned_params` to fix a
parameter to a constant and remove it from the schema, or `hidden_params` to remove it
from the schema *without* pinning (Zep's own server-side default applies).

```python
# All params model-exposed (default)
search_tool = create_search_tool(
    zep_client,
    user_id="user_123",  # OR graph_id="knowledge_base"
)

# Pin scope+limit (hidden from the model, always sent), hide reranker entirely
search_tool = create_search_tool(
    zep_client,
    user_id="user_123",
    pinned_params={"scope": "edges", "limit": 5},
    hidden_params={"reranker"},
)

# Constructor-only (never exposed to the model):
search_tool = create_search_tool(
    zep_client,
    graph_id="knowledge_base",
    search_filters={"node_labels": ["Project"]},
    bfs_origin_node_uuids=["node-uuid-1"],
)
```

The legacy `scope=`/`reranker=`/`limit=` constructor arguments still work — each pins
(and hides) its parameter, equivalent to putting it in `pinned_params`. A Zep failure
returns an error string to the model; the tool never raises into the crew.

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
- Payloads over Zep's `graph.add` ceiling are truncated to 9,900 chars (with a
  lengths-only warning) instead of failing with a 400

### Provisioning: `ensure_user` / `ensure_thread` and `on_created`

`ensure_user(client, *, user_id, first_name=None, last_name=None, email=None,
on_created=None)` and `ensure_thread(client, *, thread_id, user_id)` are idempotent,
create-then-catch-conflict helpers. Both return `True` if the resource was newly created
and `False` if it already existed; genuine failures (auth, network, 5xx) always raise.
`on_created` (a sync `Callable[[Zep, str], None]`) fires exactly once, only when the
user is genuinely new — use it for one-time per-user setup (ontology, custom
instructions):

```python
from zep_crewai import ensure_user, ensure_thread

def setup_new_user(client, user_id):
    client.graph.set_ontology(...)  # one-time per-user configuration

ensure_user(zep_client, user_id="alice_123", first_name="Alice", on_created=setup_new_user)
ensure_thread(zep_client, thread_id="project_456", user_id="alice_123")
```

`ZepUserStorage` and `ZepStorage` also provision **lazily** on the first
`save()`/`search()` call (pass `first_name`/`last_name`/`email`/`on_created` to their
constructors to feed that path). The lazy path never raises — a provisioning failure is
logged and `save()` becomes a no-op for that call — so prefer the explicit helpers above
when you want misconfiguration to fail loudly. `ZepGraphStorage` has no `on_created`:
it is scoped to a standalone `graph_id`, not a Zep user.

### Custom context: `context_builder` and `context_template`

`ZepUserStorage(context_builder=...)` replaces the default graph composition in
`search()` with your own retrieval logic. The builder is a **sync** callable receiving a
frozen `ContextInput` (`zep`, `user_id`, `thread_id`, `user_message`) and returning the
context string, or `None` for "no results". A builder exception is logged and degrades
to empty results. Persistence (`save`) is a separate, caller-driven call in CrewAI's
model, so nothing runs concurrently with the builder.

```python
from zep_crewai import ZepUserStorage, ContextInput

def my_builder(ctx: ContextInput) -> str | None:
    results = ctx.zep.graph.search(user_id=ctx.user_id, query=ctx.user_message, scope="edges")
    if not results.edges:
        return None
    return "\n".join(edge.fact for edge in results.edges)

storage = ZepUserStorage(
    client=zep_client, user_id="alice_123", thread_id="project_456",
    context_builder=my_builder,
)
```

`context_template` (on `ZepUserStorage` and `ZepGraphStorage`) wraps the context string
returned from `search()`. It must contain a literal `{context}` placeholder and is
rendered via plain `str.replace` (never `str.format`), so context containing `{`, `}`,
or `%` is always safe. The default is the canonical `<ZEP_CONTEXT>...</ZEP_CONTEXT>`
block shared across Zep integrations (`DEFAULT_CONTEXT_TEMPLATE`).

### Error handling and size limits

- **`save()` never raises.** A Zep failure during `save()` is logged and the call
  returns normally — a Zep outage never crashes the crew. Use the provisioning helpers
  out-of-band if you need loud failures.
- **Message truncation**: message content over Zep's 4,096-char thread-message limit is
  truncated to 4,000 chars before `thread.add_messages` (warning logged with lengths
  only, never content).
- **Graph payload truncation**: `graph.add` payloads are truncated to 9,900 chars
  (under Zep's 10,000-char ceiling) in the storage save paths and `ZepAddDataTool`.
- Search queries are truncated to 400 chars (Zep's query limit), as before.

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
- `thread_id`: Thread identifier (required)
- `search_filters`: Search filters (optional)
- `facts_limit`: Maximum facts for context (default: 20)
- `entity_limit`: Maximum entities for context (default: 5)
- `first_name` / `last_name` / `email`: Optional identity fields for lazy provisioning
- `on_created`: Optional hook fired once when the Zep user is newly created (lazy path)
- `context_builder`: Optional sync callable replacing the default `search()` composition
- `context_template`: Template wrapping `search()` context (default: `DEFAULT_CONTEXT_TEMPLATE`)
- `mode`: Deprecated and ignored (Zep V3 removed the thread context mode option)

#### ZepGraphStorage
- `client`: Zep client instance (required)
- `graph_id`: Graph identifier (required)
- `search_filters`: Search filters (optional)
- `facts_limit`: Maximum facts for context (default: 20)
- `entity_limit`: Maximum entities for context (default: 5)
- `context_template`: Template wrapping `search()` context (default: `DEFAULT_CONTEXT_TEMPLATE`)
- No `on_created` — graph-scoped, no Zep user to provision

### Tool Parameters

#### Search Tool (model-exposed by default; pin or hide via `pinned_params`/`hidden_params`)
- `query`: Search query string (always required, max 400 chars)
- `scope`: "edges", "nodes", "episodes", "observations", "thread_summaries", or "auto" (default: "edges")
- `reranker`: "rrf", "mmr", "node_distance", "episode_mentions", or "cross_encoder" (default: "rrf")
- `limit`: Maximum results (default: 10)
- `mmr_lambda`: Diversity/relevance balance for the "mmr" reranker (omitted when unset)
- `center_node_uuid`: Center node for "node_distance" reranking (omitted when unset)

Constructor-only: `search_filters`, `bfs_origin_node_uuids`.

#### Add Data Tool
- `data`: Content to store (truncated to 9,900 chars if over Zep's limit)
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

- Python 3.11+
- `zep-cloud>=3.23.0`
- `crewai>=1.0.0`
- `pydantic>=2.0.0`

## Best Practices

1. **Storage Selection**
   - Use `ZepUserStorage` for user-specific, personal data
   - Use `ZepGraphStorage` for shared, organizational knowledge

2. **Tool Usage**
   - Bind tools to specific users or graphs at creation
   - Pin or hide search parameters the model should not control
   - Add data with appropriate types for better organization

3. **Memory Management**
   - Set up ontologies for structured data
   - Use search filters to improve relevance
   - Combine storage types for comprehensive memory

4. **Performance**
   - Zep ingestion is asynchronous: freshly saved facts become searchable only
     after server-side extraction completes. Because CrewAI storage adapters
     `save()` one item per call, each call produces its own extraction episode
     and single-message episodes can sit in Zep's coalescing window for several
     minutes before facts appear — design for eventual availability rather
     than read-after-write
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