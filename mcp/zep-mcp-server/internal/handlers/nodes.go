package handlers

import (
	"context"
	"fmt"

	zep "github.com/getzep/zep-go/v3"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleGetUserNodes handles the get_user_nodes tool
func HandleGetUserNodes(client *zepclient.Client) mcp.ToolHandlerFor[GetUserNodesInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input GetUserNodesInput) (*mcp.CallToolResult, any, error) {
		// Apply defaults
		limit := input.Limit
		if limit == 0 {
			limit = 20
		}

		// Build request
		// Note: SDK does not support label filtering at this level
		nodeReq := &zep.GraphNodesRequest{
			Limit: &limit,
		}

		// Get user nodes
		nodes, err := client.Graph.Node.GetByUserID(ctx, input.UserID, nodeReq)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to get user nodes: %w", err)
		}

		// Format results as JSON
		resultJSON, err := transform.FormatJSON(nodes)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, nodes, nil
	}
}
