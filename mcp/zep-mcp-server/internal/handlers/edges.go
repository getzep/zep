package handlers

import (
	"context"
	"fmt"

	zep "github.com/getzep/zep-go/v3"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleGetUserEdges handles the get_user_edges tool
func HandleGetUserEdges(client *zepclient.Client) mcp.ToolHandlerFor[GetUserEdgesInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input GetUserEdgesInput) (*mcp.CallToolResult, any, error) {
		// Apply defaults
		limit := input.Limit
		if limit == 0 {
			limit = 20
		}

		// Build request
		// Note: SDK does not support edge type filtering at this level
		edgeReq := &zep.GraphEdgesRequest{
			Limit: &limit,
		}

		// Get user edges
		edges, err := client.Graph.Edge.GetByUserID(ctx, input.UserID, edgeReq)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to get user edges: %w", err)
		}

		// Format results as JSON
		resultJSON, err := transform.FormatJSON(edges)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, edges, nil
	}
}
