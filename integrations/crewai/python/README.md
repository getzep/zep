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

## Identifiers: Zep v4 addresses every resource by UUID

Zep v4 gives every user, thread, and graph a server-generated UUID. A
`user_id` or a `thread_id` is a name, not an address. The public API of this
package therefore takes `user_uuid`, `thread_uuid`, and `graph_uuid`.

Create each resource one time, read the UUID from the create response, and
store the UUID in your own database. The integration does not resolve a name
at run time.

## Quick Start

### User Storage with Conversation Memory

```python
import os
from zep_cloud.client import Zep
from zep_crewai import ZepUserStorage, create_search_tool
from crewai import Agent, Crew, Task

# Initialize Zep client
zep_client = Zep(api_key=os.getenv("ZEP_API_KEY"))

# Create the user and the thread one time, then store the UUIDs
user = zep_client.user.create(first_name="Alice", email="alice@example.com")
thread = zep_client.thread.create(user_uuid=user.uuid_)

# Create user storage
user_storage = ZepUserStorage(
    client=zep_client,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,  # for conversation context
    graph_uuid=user.graph_uuid,  # the user graph
)

# Persist conversation turns and business data
user_storage.save("How can I help?", metadata={"type": "message", "role": "assistant"})

# Give an agent a Zep search tool so it can retrieve context on demand
agent = Agent(
    role="Personal Assistant",
    tools=[create_search_tool(zep_client, graph_uuid=user.graph_uuid)],
)

crew = Crew(agents=[agent], tasks=[...])
```

### Knowledge Graph Storage

```python
from zep_crewai import ZepGraphStorage, create_search_tool

# Create the graph one time, then store its UUID
graph = zep_client.graph.create(name="company knowledge")

# Create graph storage for shared knowledge
graph_storage = ZepGraphStorage(
    client=zep_client,
    graph_uuid=graph.uuid_,
    search_filters={"node_labels": ["Technology", "Project"]},
)

# Persist knowledge, then let agents search it through a tool
graph_storage.save("Project Alpha uses Python and React", metadata={"type": "text"})

agent = Agent(
    role="Knowledge Assistant",
    tools=[create_search_tool(zep_client, graph_uuid=graph.uuid_)],
)

crew = Crew(agents=[agent], tasks=[...])
```

### Tool-Equipped Agents

```python
from zep_crewai import create_search_tool, create_add_data_tool

# Both tools are bound to one graph. For a user graph, pass the user's graph_uuid.
search_tool = create_search_tool(zep_client, graph_uuid=user.graph_uuid)
add_tool = create_add_data_tool(zep_client, graph_uuid=graph.uuid_)

# Create agent with Zep tools
agent = Agent(
    role="Knowledge Assistant",
    goal="Manage and retrieve information efficiently",
    tools=[search_tool, add_tool],
    llm="gpt-5-mini",
)
```

## Features

### Storage Classes

#### ZepUserStorage
Manages user-specific memories and conversations:
- **Thread Messages**: Conversation history with role-based storage
- **User Graph**: Personal knowledge, preferences, and context
- **Search Filters**: Target specific node types and relationships
- **Thread Context**: Uses `thread.get_context(thread_uuid)` to return Zep's auto-assembled Context Block

#### ZepGraphStorage  
Manages generic knowledge graphs for shared information:
- **Structured Knowledge**: Store entities with defined ontologies
- **Multi-scope Search**: Search edges (facts), nodes (entities), and episodes
- **Search Filters**: Filter by node labels and attributes
- **Persistent Storage**: Knowledge persists across sessions
- **Context Block**: Uses `graph.get_context(graph_uuid)`, which Zep assembles on the server

### Tool Integration

#### Search Tool (pin-or-expose)

Every graph search parameter — `scope` (`edges`, `nodes`, `episodes`, `observations`,
`thread_summaries`, `auto`), `reranker` (`rrf`, `mmr`, `node_distance`,
`episode_mentions`, `cross_encoder`), `limit`, `mmr_lambda`, `center_node_uuid` — is
exposed to the model in the tool's schema by default. Use `pinned_params` to fix a
parameter to a constant and remove it from the schema, or `hidden_params` to remove it
from the schema *without* pinning (Zep's own server-side default applies).

Zep v4 gives one search method for each scope, so `scope` selects the SDK method that
the tool calls: `graph.search_edges`, `graph.search_nodes`, `graph.search_episodes`,
`graph.search_observations`, or `graph.search_thread_summaries`. Scope `auto` calls
`graph.get_context` and returns the assembled Context Block.

```python
# All params model-exposed (default)
search_tool = create_search_tool(
    zep_client,
    graph_uuid=user.graph_uuid,  # OR the UUID of a standalone graph
)

# Pin scope+limit (hidden from the model, always sent), hide reranker entirely
search_tool = create_search_tool(
    zep_client,
    graph_uuid=user.graph_uuid,
    pinned_params={"scope": "edges", "limit": 5},
    hidden_params={"reranker"},
)

# Constructor-only (never exposed to the model):
search_tool = create_search_tool(
    zep_client,
    graph_uuid=graph.uuid_,
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
    graph_uuid=graph.uuid_,  # OR the graph_uuid of a user
)
```
- Add text, JSON, or message data
- Automatic type detection
- Structured data support
- Payloads over Zep's episode ceiling are truncated to 9,900 chars (with a
  lengths-only warning) instead of failing with a 400

### Provisioning: `ensure_user` / `ensure_thread` and `on_created`

`ensure_user(client, *, user_id, first_name=None, last_name=None, email=None,
on_created=None)` and `ensure_thread(client, *, thread_id, user_uuid)` are idempotent,
create-then-catch-conflict helpers for **onboarding only**. Each returns a tuple of the
resource and a flag: the flag is `True` when the resource was newly created and `False`
when the resource already existed. Genuine failures (auth, network, 5xx) always raise.
Read `user.uuid_`, `user.graph_uuid`, and `thread.uuid_` from the result and store them
in your own database.

`on_created` (a sync `Callable[[Zep, User], None]`) fires exactly once, only when the
user is genuinely new — use it for one-time per-user setup (ontology, custom
instructions):

```python
from zep_crewai import ensure_user, ensure_thread


def setup_new_user(client, user):
    client.graph.set_ontology(user.graph_uuid, entity_types=[...])  # one-time setup


user, created = ensure_user(
    zep_client, user_id="alice_123", first_name="Alice", on_created=setup_new_user
)
thread, _ = ensure_thread(zep_client, thread_id="project_456", user_uuid=user.uuid_)
```

The storage adapters never create a user or a thread. They take UUIDs of resources that
already exist, so a misconfiguration fails during onboarding rather than on the turn
path. `ZepGraphStorage` has no `on_created`: it is scoped to a standalone graph, not a
Zep user.

### Custom context: `context_builder` and `context_template`

`ZepUserStorage(context_builder=...)` replaces the default Context Block retrieval in
`search()` with your own retrieval logic. The builder is a **sync** callable receiving a
frozen `ContextInput` (`zep`, `user_uuid`, `thread_uuid`, `graph_uuid`, `user_message`)
and returning the context string, or `None` for "no results". A builder exception is
logged and degrades to empty results. Persistence (`save`) is a separate, caller-driven
call in CrewAI's model, so nothing runs concurrently with the builder.

```python
from zep_crewai import ZepUserStorage, ContextInput


def my_builder(ctx: ContextInput) -> str | None:
    edges = list(ctx.zep.graph.search_edges(ctx.graph_uuid, query=ctx.user_message, limit=10))
    if not edges:
        return None
    return "\n".join(edge.fact for edge in edges if edge.fact)


storage = ZepUserStorage(
    client=zep_client,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.graph_uuid,
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
- **Graph payload truncation**: `graph.episode.add` payloads are truncated to 9,900
  chars (under Zep's 10,000-char ceiling) in the storage save paths and
  `ZepAddDataTool`.
- Search queries are truncated to 400 chars (Zep's query limit), as before.

## Advanced Usage

### Graph Storage with Ontology

Define structured entities for better organization:

```python
from zep_cloud.types import EntityProperty, EntityType

project_entity = EntityType(
    name="Project",
    description="a project that a team delivers",
    properties=[
        EntityProperty(name="status", type="text", description="project status"),
        EntityProperty(name="priority", type="text", description="priority level"),
        EntityProperty(name="team_size", type="text", description="team size"),
    ],
)

# Set ontology
graph = zep_client.graph.create(name="projects")
zep_client.graph.set_ontology(graph.uuid_, entity_types=[project_entity])

# Use with filtered search and a Context Block size limit
graph_storage = ZepGraphStorage(
    client=zep_client,
    graph_uuid=graph.uuid_,
    search_filters={"node_labels": ["Project"]},
    max_characters=4000,  # Max length of the Context Block
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
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.graph_uuid,
    max_characters=4000,  # Max length of the Context Block
)

# Get the Context Block for the thread (auto-assembled by Zep)
context = personal_storage.get_context()
print(context)  # Prompt-ready Context Block string

# Shared knowledge graph for team agent
team_graph = zep_client.graph.create(name="team knowledge")
team_storage = ZepGraphStorage(client=zep_client, graph_uuid=team_graph.uuid_)

# Create agents with different storage
personal_agent = Agent(
    name="Personal Assistant",
    tools=[create_search_tool(zep_client, graph_uuid=user.graph_uuid)],
)

team_agent = Agent(
    name="Team Coordinator",
    tools=[create_search_tool(zep_client, graph_uuid=team_graph.uuid_)],
)
```

### Storage Routing

Different data types are automatically routed:

```python
# Messages go to the thread
user_storage.save(
    "How can I help you today?", metadata={"type": "message", "role": "assistant", "name": "Helper"}
)

# JSON data goes to graph
user_storage.save(
    '{"project": "Alpha", "status": "active", "budget": 50000}', metadata={"type": "json"}
)

# Text data goes to graph
user_storage.save("Project Alpha requires Python and React expertise", metadata={"type": "text"})
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
user_storage = ZepUserStorage(
    client=zep_client,
    user_uuid=user.uuid_,
    thread_uuid=thread.uuid_,
    graph_uuid=user.graph_uuid,
)
user_storage.save("User prefers morning meetings", metadata={"type": "text"})

# Agent retrieves relevant context via a Zep search tool
personal_assistant = Agent(
    role="Personal Assistant",
    tools=[create_search_tool(zep_client, graph_uuid=user.graph_uuid)],
    backstory="You know the user's preferences and history",
)
```

#### Knowledge Base Management
```python
# Shared knowledge with search tools
knowledge_graph = zep_client.graph.create(name="knowledge base")
knowledge_tools = [
    create_search_tool(zep_client, graph_uuid=knowledge_graph.uuid_),
    create_add_data_tool(zep_client, graph_uuid=knowledge_graph.uuid_),
]

curator = Agent(
    role="Knowledge Curator",
    tools=knowledge_tools,
    backstory="You maintain the organization's knowledge base",
)
```

#### Multi-Modal Memory
```python
# Combine user and graph storage with tools
research_graph = zep_client.graph.create(name="research findings")
research_agent = Agent(
    role="Research Analyst",
    tools=[
        create_search_tool(zep_client, graph_uuid=user.graph_uuid),
        create_search_tool(zep_client, graph_uuid=research_graph.uuid_),
    ],
    backstory="You analyze both personal and organizational data",
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
- `user_uuid`: The UUID of an existing Zep user (required)
- `thread_uuid`: The UUID of an existing Zep thread (required)
- `graph_uuid`: The UUID of the user graph (optional; when it is not given, the
  storage reads it one time with `user.get(user_uuid)` and caches it)
- `search_filters`: Search filters (optional)
- `max_characters`: Maximum length of the Context Block that `search()` retrieves
- `context_builder`: Optional sync callable replacing the default `search()` retrieval
- `context_template`: Template wrapping `search()` context (default: `DEFAULT_CONTEXT_TEMPLATE`)
- `mode`: Deprecated and ignored (Zep removed the thread context mode option)

#### ZepGraphStorage
- `client`: Zep client instance (required)
- `graph_uuid`: The UUID of an existing graph (required)
- `search_filters`: Search filters (optional)
- `max_characters`: Maximum length of the Context Block that `search()` retrieves
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
- `zep-cloud==4.0.0a5`
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