package handlers

import (
	"context"
	"fmt"

	zep "github.com/getzep/zep-go/v3"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleGetUserContext handles the get_user_context tool
func HandleGetUserContext(client *zepclient.Client) mcp.ToolHandlerFor[GetUserContextInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input GetUserContextInput) (*mcp.CallToolResult, any, error) {
		// Build request
		contextReq := &zep.ThreadGetUserContextRequest{}

		if input.TemplateID != "" {
			contextReq.TemplateID = &input.TemplateID
		}

		// Get user context
		memory, err := client.Thread.GetUserContext(ctx, input.ThreadID, contextReq)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to get user context: %w", err)
		}

		// Format results as JSON
		resultJSON, err := transform.FormatJSON(memory)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, memory, nil
	}
}
