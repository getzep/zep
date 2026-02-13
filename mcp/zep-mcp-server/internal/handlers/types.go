package handlers

// Input and output types for MCP tool handlers
// These types are used to automatically generate JSON schemas

// SearchGraphInput defines the input parameters for search_graph
type SearchGraphInput struct {
	UserID   string  `json:"user_id" jsonschema:"The user ID to search for"`
	Query    string  `json:"query" jsonschema:"The search query string"`
	Scope    string  `json:"scope,omitempty" jsonschema:"Search scope: nodes or edges (default: edges)"`
	Limit    int     `json:"limit,omitempty" jsonschema:"Maximum number of results to return (default: 10)"`
	Reranker string  `json:"reranker,omitempty" jsonschema:"Reranking strategy: rrf, mmr, or node_distance"`
	MinScore float64 `json:"min_score,omitempty" jsonschema:"Minimum relevance score threshold (0.0-1.0)"`
}

// GetUserContextInput defines the input parameters for get_user_context
type GetUserContextInput struct {
	ThreadID string `json:"thread_id" jsonschema:"The thread ID for which to retrieve context"`
	Mode     string `json:"mode,omitempty" jsonschema:"Context mode: summary, basic, or full (default: summary)"`
}

// GetUserInput defines the input parameters for get_user
type GetUserInput struct {
	UserID string `json:"user_id" jsonschema:"The user ID to retrieve"`
}

// ListThreadsInput defines the input parameters for list_threads
type ListThreadsInput struct {
	UserID string `json:"user_id" jsonschema:"The user ID for which to list threads"`
	Limit  int    `json:"limit,omitempty" jsonschema:"Maximum number of threads to return (default: 20)"`
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
