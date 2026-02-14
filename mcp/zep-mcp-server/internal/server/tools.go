package server

import "github.com/modelcontextprotocol/go-sdk/mcp"

// Tool definitions for the Zep MCP server
// InputSchemas are automatically generated from the handler input types

var SearchGraphTool = &mcp.Tool{
	Name:        "search_graph",
	Description: "Search the Zep knowledge graph for relevant information about a user. Returns facts, entities, and relationships.",
}

var GetUserContextTool = &mcp.Tool{
	Name:        "get_user_context",
	Description: "Retrieve formatted context string for a conversation thread. Returns facts and entities relevant to the thread.",
}

var GetUserTool = &mcp.Tool{
	Name:        "get_user",
	Description: "Retrieve user information and metadata from Zep.",
}

var ListThreadsTool = &mcp.Tool{
	Name:        "list_threads",
	Description: "List all conversation threads for a specific user.",
}

var GetUserNodesTool = &mcp.Tool{
	Name:        "get_user_nodes",
	Description: "Retrieve knowledge graph nodes (entities) associated with a user.",
}

var GetUserEdgesTool = &mcp.Tool{
	Name:        "get_user_edges",
	Description: "Retrieve knowledge graph edges (relationships/facts) associated with a user.",
}

var GetEpisodesTool = &mcp.Tool{
	Name:        "get_episodes",
	Description: "Retrieve temporal episodes (events) from a user's knowledge graph history.",
}

var GetThreadMessagesTool = &mcp.Tool{
	Name:        "get_thread_messages",
	Description: "Retrieve messages from a conversation thread.",
}

var GetNodeTool = &mcp.Tool{
	Name:        "get_node",
	Description: "Retrieve a specific knowledge graph node (entity) by its UUID.",
}

var GetEdgeTool = &mcp.Tool{
	Name:        "get_edge",
	Description: "Retrieve a specific knowledge graph edge (relationship/fact) by its UUID.",
}

var GetEpisodeTool = &mcp.Tool{
	Name:        "get_episode",
	Description: "Retrieve a specific episode by its UUID.",
}

var GetNodeEdgesTool = &mcp.Tool{
	Name:        "get_node_edges",
	Description: "Retrieve all edges (relationships/facts) connected to a specific node.",
}

var GetEpisodeMentionsTool = &mcp.Tool{
	Name:        "get_episode_mentions",
	Description: "Retrieve nodes and edges mentioned in a specific episode.",
}
