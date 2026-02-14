package handlers

import (
	"context"
	"fmt"

	zep "github.com/getzep/zep-go/v3"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleGetThreadMessages handles the get_thread_messages tool
func HandleGetThreadMessages(client *zepclient.Client) mcp.ToolHandlerFor[GetThreadMessagesInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input GetThreadMessagesInput) (*mcp.CallToolResult, any, error) {
		getReq := &zep.ThreadGetRequest{}

		if input.Lastn > 0 {
			getReq.Lastn = &input.Lastn
		}

		if input.Limit > 0 {
			getReq.Limit = &input.Limit
		}

		messages, err := client.Thread.Get(ctx, input.ThreadID, getReq)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to get thread messages: %w", err)
		}

		resultJSON, err := transform.FormatJSON(messages)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, messages, nil
	}
}
