package handlers

import (
	"context"
	"fmt"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleListThreads handles the list_threads tool
func HandleListThreads(client *zepclient.Client) mcp.ToolHandlerFor[ListThreadsInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input ListThreadsInput) (*mcp.CallToolResult, any, error) {
		threads, err := client.User.GetThreads(ctx, input.UserID)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to list threads: %w", err)
		}

		resultJSON, err := transform.FormatJSON(threads)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, threads, nil
	}
}
