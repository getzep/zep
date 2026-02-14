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
- [get_thread_messages](#get_thread_messages)
- [get_node](#get_node)
- [get_edge](#get_edge)
- [get_episode](#get_episode)
- [get_node_edges](#get_node_edges)
- [get_episode_mentions](#get_episode_mentions)

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
| `min_fact_rating` | number | No | Minimum fact rating threshold for filtering results |
| `mmr_lambda` | number | No | Weighting for maximal marginal relevance reranking |
| `center_node_uuid` | string | No | Node UUID to rerank around for `node_distance` reranking |
| `node_labels` | array[string] | No | Filter results by node labels |
| `edge_types` | array[string] | No | Filter results by edge types |

### Returns

JSON object containing edges, nodes, and/or episodes depending on the search scope.

### Example

```javascript
{
  "user_id": "alice_123",
  "query": "What are Alice's dietary preferences?",
  "scope": "edges",
  "limit": 5,
  "reranker": "cross_encoder",
  "edge_types": ["PREFERS", "LIKES"]
}
```

### Use Cases

- Finding specific facts about a user
- Retrieving relationship information
- Discovering relevant entities
- Building context for conversations
- Filtering by entity or relationship type

---

## get_user_context

Retrieve formatted context string for a conversation thread. Returns facts and entities relevant to the thread.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `thread_id` | string | Yes | Thread (conversation) identifier |
| `template_id` | string | No | Context template ID for custom context rendering |

### Returns

JSON object containing:
- `context`: Formatted context string with facts and entities

### Example

```javascript
{
  "thread_id": "conversation_456",
  "template_id": "my_custom_template"
}
```

### Use Cases

- Getting conversation-relevant context for prompts
- Building context-aware AI responses
- Using custom templates for structured context output

---

## get_user

Retrieve user information and metadata from Zep.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User identifier |

### Returns

JSON object containing user properties including `user_id`, `email`, `first_name`, `last_name`, `created_at`, and associated metadata.

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

List all conversation threads for a specific user.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User identifier |

### Returns

JSON array of thread objects containing `thread_id`, `user_id`, `created_at`, `updated_at`, and metadata.

### Example

```javascript
{
  "user_id": "alice_123"
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
| `limit` | integer | No | Maximum nodes to return (default: 20) |

### Returns

JSON array of node objects containing `uuid`, `name`, `labels`, `summary`, `created_at`, and additional properties.

### Example

```javascript
{
  "user_id": "alice_123",
  "limit": 10
}
```

### Use Cases

- Exploring entities in user's knowledge graph
- Understanding user's context and relationships
- Finding specific types of entities

---

## get_user_edges

Retrieve edges (relationships/facts) from a user's knowledge graph.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User identifier |
| `limit` | integer | No | Maximum edges to return (default: 20) |

### Returns

JSON array of edge objects containing `uuid`, `source_node_uuid`, `target_node_uuid`, `edge_type`, `fact`, `valid_at`, and `created_at`.

### Example

```javascript
{
  "user_id": "alice_123",
  "limit": 10
}
```

### Use Cases

- Examining relationships between entities
- Understanding fact provenance
- Temporal fact analysis

---

## get_episodes

Retrieve temporal episodes (events) from a user's knowledge graph history.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | Yes | User identifier |
| `lastn` | integer | No | Return the last N episodes (default: 10) |

### Returns

JSON array of episode objects containing `uuid`, `content`, `source`, `created_at`, and metadata.

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

---

## get_thread_messages

Retrieve messages from a conversation thread.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `thread_id` | string | Yes | Thread identifier |
| `lastn` | integer | No | Number of most recent messages to return (overrides limit) |
| `limit` | integer | No | Maximum number of messages to return |

### Returns

JSON object containing messages with `role`, `content`, `uuid`, and timestamps.

### Example

```javascript
{
  "thread_id": "conversation_456",
  "lastn": 10
}
```

### Use Cases

- Reading conversation history
- Retrieving recent messages for context
- Auditing conversation content

---

## get_node

Retrieve a specific knowledge graph node (entity) by its UUID.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uuid` | string | Yes | The UUID of the node to retrieve |

### Returns

JSON object containing the full node details including `uuid`, `name`, `labels`, `summary`, and properties.

### Example

```javascript
{
  "uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Use Cases

- Getting full details of a node discovered via search
- Following up on entities returned by other tools

---

## get_edge

Retrieve a specific knowledge graph edge (relationship/fact) by its UUID.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uuid` | string | Yes | The UUID of the edge to retrieve |

### Returns

JSON object containing the full edge details including `uuid`, `source_node_uuid`, `target_node_uuid`, `edge_type`, `fact`, and temporal data.

### Example

```javascript
{
  "uuid": "550e8400-e29b-41d4-a716-446655440001"
}
```

### Use Cases

- Getting full details of an edge discovered via search
- Understanding a specific relationship or fact

---

## get_episode

Retrieve a specific episode by its UUID.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uuid` | string | Yes | The UUID of the episode to retrieve |

### Returns

JSON object containing the full episode details including `uuid`, `content`, `source`, and metadata.

### Example

```javascript
{
  "uuid": "550e8400-e29b-41d4-a716-446655440002"
}
```

### Use Cases

- Getting full details of a specific episode
- Examining episode content and metadata

---

## get_node_edges

Retrieve all edges (relationships/facts) connected to a specific node.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `node_uuid` | string | Yes | The UUID of the node to get edges for |

### Returns

JSON array of edge objects connected to the specified node.

### Example

```javascript
{
  "node_uuid": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Use Cases

- Exploring all relationships of a specific entity
- Graph traversal from a node
- Understanding an entity's connections

---

## get_episode_mentions

Retrieve nodes and edges mentioned in a specific episode.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `uuid` | string | Yes | The UUID of the episode |

### Returns

JSON object containing `nodes` and `edges` arrays — the entities and relationships mentioned in the episode.

### Example

```javascript
{
  "uuid": "550e8400-e29b-41d4-a716-446655440002"
}
```

### Use Cases

- Understanding what entities and facts were extracted from an episode
- Tracing data provenance from episodes to graph elements
- Auditing knowledge graph construction

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

// 4. Drill into a specific node
const node = await tools.get_node({
  uuid: preferences.nodes[0].uuid
});

// 5. Get all relationships for that node
const relationships = await tools.get_node_edges({
  node_uuid: node.uuid
});
```

### Error Handling

All tools return standard MCP errors:

- **400 Bad Request**: Invalid parameters
- **401 Unauthorized**: Invalid API key
- **404 Not Found**: User, thread, or resource not found
- **500 Internal Server Error**: Server or API error

### Performance Optimization

- Limit results with `limit` and `lastn` parameters
- Use search filters (`node_labels`, `edge_types`) to narrow results
- Use specific UUIDs when you know the exact entity you need

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
