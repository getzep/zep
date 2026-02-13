# Tool Reference

Complete reference for all tools provided by the Zep MCP Server.

## Table of Contents

- [search_graph](#search_graph)
- [get_user_context](#get_user_context)
- [get_user](#get_user)
- [list_threads](#list_threads)
- [get_user_nodes](#get_user_nodes)
- [get_user_edges](#get_user_edges)
- [get_episodes](#get_episodes)

---

## search_graph

Search the Zep knowledge graph for relevant information about a user.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User ID to search within |
| `query` | string | Yes | Search query text |
| `scope` | string | No | Search scope: `edges`, `nodes`, or `episodes` (default: `edges`) |
| `limit` | integer | No | Maximum results (default: 10, max: 50) |
| `reranker` | string | No | Reranking strategy: `rrf`, `mmr`, `node_distance`, `episode_mentions`, `cross_encoder` |
| `min_score` | number | No | Minimum relevance score threshold (0.0-1.0) |

### Returns

JSON array of search results containing facts/summaries, nodes, edges, and relevance scores.

### Example

```javascript
{
  "user_id": "alice_123",
  "query": "What are Alice's dietary preferences?",
  "scope": "edges",
  "limit": 5,
  "reranker": "cross_encoder"
}
```

### Use Cases

- Finding specific facts about a user
- Retrieving relationship information
- Discovering relevant entities
- Building context for conversations

---

## get_user_context

Retrieve formatted context string for a conversation thread. Returns facts and entities relevant to the thread.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `thread_id` | string | Yes | Thread (conversation) identifier |
| `mode` | string | No | Context mode: `summary` (detailed) or `basic` (faster, default: `summary`) |

### Returns

JSON object containing:
- `context`: Formatted context string with facts and entities
- `relevant_facts`: Array of facts with ratings and temporal information

### Example

```javascript
{
  "thread_id": "conversation_456",
  "mode": "summary"
}
```

### Use Cases

- Getting conversation-relevant context for prompts
- Building context-aware AI responses
- Understanding user preferences in context

### Performance Tips

- Use `mode: "basic"` for faster retrieval (P95 < 200ms)
- Use `mode: "summary"` for more detailed context

---

## get_user

Retrieve user information and metadata from Zep.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User identifier |

### Returns

JSON object containing:
- `user_id`: The user identifier
- `created_at`: Timestamp of user creation
- `updated_at`: Timestamp of last update
- `metadata`: Custom metadata object
- Additional user properties

### Example

```javascript
{
  "user_id": "alice_123"
}
```

### Use Cases

- Getting user profile information
- Checking if a user exists
- Retrieving user metadata

---

## list_threads

List conversation threads for a specific user.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User identifier |
| `limit` | integer | No | Maximum threads to return (default: 20, max: 100) |

### Returns

JSON array of thread objects containing:
- `thread_id`: Thread identifier
- `user_id`: Associated user
- `created_at`: Thread creation timestamp
- `updated_at`: Last update timestamp
- `metadata`: Custom metadata

### Example

```javascript
{
  "user_id": "alice_123",
  "limit": 10
}
```

### Use Cases

- Browsing user's conversation history
- Thread discovery
- Analyzing conversation patterns

---

## get_user_nodes

Retrieve entity nodes from a user's knowledge graph. Nodes represent people, places, concepts, and other entities.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User identifier |
| `labels` | array[string] | No | Filter by node labels (entity types) |
| `limit` | integer | No | Maximum nodes to return (default: 20, max: 100) |

### Returns

JSON array of node objects containing:
- `uuid`: Node identifier
- `name`: Entity name
- `labels`: Node type labels
- `summary`: Entity summary
- `created_at`: Creation timestamp
- Additional node properties

### Example

```javascript
{
  "user_id": "alice_123",
  "labels": ["Person", "Organization"],
  "limit": 10
}
```

### Use Cases

- Exploring entities in user's knowledge graph
- Understanding user's context and relationships
- Finding specific types of entities

---

## get_user_edges

Retrieve edges (relationships) from a user's knowledge graph. Edges represent facts and connections between entities.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User identifier |
| `edge_types` | array[string] | No | Filter by edge types (relationship types) |
| `limit` | integer | No | Maximum edges to return (default: 20, max: 100) |

### Returns

JSON array of edge objects containing:
- `uuid`: Edge identifier
- `source_node_uuid`: Source entity
- `target_node_uuid`: Target entity
- `edge_type`: Relationship type
- `fact`: The fact/relationship description
- `valid_at`: Temporal validity information
- `created_at`: Creation timestamp

### Example

```javascript
{
  "user_id": "alice_123",
  "edge_types": ["KNOWS", "WORKS_AT", "LIKES"],
  "limit": 10
}
```

### Use Cases

- Examining relationships between entities
- Understanding fact provenance
- Temporal fact analysis
- Building relationship graphs

---

## get_episodes

Retrieve episode nodes (data ingestion events) for a user. Episodes represent conversation history and other temporal data.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User identifier |
| `lastn` | integer | No | Return the last N episodes (default: 10, max: 100) |

### Returns

JSON array of episode objects containing:
- `uuid`: Episode identifier
- `content`: Episode content/data
- `source`: Data source
- `created_at`: Ingestion timestamp
- `metadata`: Episode metadata

### Example

```javascript
{
  "user_id": "alice_123",
  "lastn": 5
}
```

### Use Cases

- Accessing temporal data ingestion events
- Understanding conversation history context
- Analyzing data provenance
- Debugging data ingestion

---

## Common Patterns

### Combining Tools

Tools can be combined for powerful workflows:

```javascript
// 1. Get user threads
const threads = await tools.list_threads({ user_id: "alice_123" });

// 2. Get context for most recent thread
const context = await tools.get_user_context({
  thread_id: threads[0].thread_id
});

// 3. Search graph for specific information
const preferences = await tools.search_graph({
  user_id: "alice_123",
  query: "dietary preferences",
  scope: "edges"
});
```

### Error Handling

All tools return standard MCP errors:

- **400 Bad Request**: Invalid parameters
- **401 Unauthorized**: Invalid API key
- **404 Not Found**: User, thread, or resource not found
- **500 Internal Server Error**: Server or API error

### Performance Optimization

- Use `basic` mode in `get_user_context` for faster retrieval
- Limit results with `limit` parameters
- Use specific filters (`labels`, `edge_types`) to reduce result size
- Cache frequently accessed data in your application

### Temporal Queries

Many results include temporal information:

- **`valid_at`**: When a fact is valid (edges)
- **`created_at`**: When data was created
- **`updated_at`**: When data was last modified

Use this information for temporal reasoning and context.

## Need Help?

- [Zep Cloud Documentation](https://help.getzep.com/)
- [GitHub Issues](https://github.com/getzep/zep/issues)
- [Discord Community](https://discord.gg/W8Kw6bsgXQ)
