package handlers

import (
	"context"
	"fmt"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleGetNode handles the get_node tool
func HandleGetNode(client *zepclient.Client) mcp.ToolHandlerFor[GetNodeInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input GetNodeInput) (*mcp.CallToolResult, any, error) {
		node, err := client.Graph.Node.Get(ctx, input.UUID)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to get node: %w", err)
		}

		resultJSON, err := transform.FormatJSON(node)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, node, nil
	}
}
