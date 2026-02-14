package handlers

// Input and output types for MCP tool handlers
// These types are used to automatically generate JSON schemas

// SearchGraphInput defines the input parameters for search_graph
type SearchGraphInput struct {
	UserID         string   `json:"user_id" jsonschema:"The user ID to search for"`
	Query          string   `json:"query" jsonschema:"The search query string"`
	Scope          string   `json:"scope,omitempty" jsonschema:"Search scope: edges nodes or episodes (default: edges)"`
	Limit          int      `json:"limit,omitempty" jsonschema:"Maximum number of results to return (default: 10)"`
	Reranker       string   `json:"reranker,omitempty" jsonschema:"Reranking strategy: rrf mmr node_distance episode_mentions or cross_encoder"`
	MinFactRating  float64  `json:"min_fact_rating,omitempty" jsonschema:"Minimum fact rating threshold for filtering results"`
	MmrLambda      float64  `json:"mmr_lambda,omitempty" jsonschema:"Weighting for maximal marginal relevance reranking"`
	CenterNodeUUID string   `json:"center_node_uuid,omitempty" jsonschema:"Node UUID to rerank around for node_distance reranking"`
	NodeLabels     []string `json:"node_labels,omitempty" jsonschema:"Filter results by node labels"`
	EdgeTypes      []string `json:"edge_types,omitempty" jsonschema:"Filter results by edge types"`
}

// GetUserContextInput defines the input parameters for get_user_context
type GetUserContextInput struct {
	ThreadID   string `json:"thread_id" jsonschema:"The thread ID for which to retrieve context"`
	TemplateID string `json:"template_id,omitempty" jsonschema:"Optional context template ID for custom context rendering"`
}

// GetUserInput defines the input parameters for get_user
type GetUserInput struct {
	UserID string `json:"user_id" jsonschema:"The user ID to retrieve"`
}

// ListThreadsInput defines the input parameters for list_threads
type ListThreadsInput struct {
	UserID string `json:"user_id" jsonschema:"The user ID for which to list threads"`
}

// GetUserNodesInput defines the input parameters for get_user_nodes
type GetUserNodesInput struct {
	UserID string `json:"user_id" jsonschema:"The user ID for which to retrieve nodes"`
	Limit  int    `json:"limit,omitempty" jsonschema:"Maximum number of nodes to return (default: 20)"`
}

// GetUserEdgesInput defines the input parameters for get_user_edges
type GetUserEdgesInput struct {
	UserID string `json:"user_id" jsonschema:"The user ID for which to retrieve edges"`
	Limit  int    `json:"limit,omitempty" jsonschema:"Maximum number of edges to return (default: 20)"`
}

// GetEpisodesInput defines the input parameters for get_episodes
type GetEpisodesInput struct {
	UserID string `json:"user_id" jsonschema:"The user ID for which to retrieve episodes"`
	Lastn  int    `json:"lastn,omitempty" jsonschema:"Number of most recent episodes to retrieve (default: 10)"`
}

// GetThreadMessagesInput defines the input parameters for get_thread_messages
type GetThreadMessagesInput struct {
	ThreadID string `json:"thread_id" jsonschema:"The thread ID to retrieve messages from"`
	Lastn    int    `json:"lastn,omitempty" jsonschema:"Number of most recent messages to return"`
	Limit    int    `json:"limit,omitempty" jsonschema:"Maximum number of messages to return"`
}

// GetNodeInput defines the input parameters for get_node
type GetNodeInput struct {
	UUID string `json:"uuid" jsonschema:"The UUID of the node to retrieve"`
}

// GetEdgeInput defines the input parameters for get_edge
type GetEdgeInput struct {
	UUID string `json:"uuid" jsonschema:"The UUID of the edge to retrieve"`
}

// GetEpisodeInput defines the input parameters for get_episode
type GetEpisodeInput struct {
	UUID string `json:"uuid" jsonschema:"The UUID of the episode to retrieve"`
}

// GetNodeEdgesInput defines the input parameters for get_node_edges
type GetNodeEdgesInput struct {
	NodeUUID string `json:"node_uuid" jsonschema:"The UUID of the node to get edges for"`
}

// GetEpisodeMentionsInput defines the input parameters for get_episode_mentions
type GetEpisodeMentionsInput struct {
	UUID string `json:"uuid" jsonschema:"The UUID of the episode"`
}
