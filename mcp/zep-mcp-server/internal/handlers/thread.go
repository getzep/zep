package handlers

import (
	"context"
	"fmt"

	zep "github.com/getzep/zep-go/v3"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleListThreads handles the list_threads tool
func HandleListThreads(client *zepclient.Client) mcp.ToolHandlerFor[ListThreadsInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input ListThreadsInput) (*mcp.CallToolResult, any, error) {
		// Apply defaults
		pageSize := input.Limit
		if pageSize == 0 {
			pageSize = 20
		}

		// List all threads
		// Note: SDK ListAll returns all threads, not filtered by user
		// We'll need to filter the results by user_id after fetching
		listReq := &zep.ThreadListAllRequest{
			PageSize: &pageSize,
		}

		response, err := client.Thread.ListAll(ctx, listReq)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to list threads: %w", err)
		}

		// Filter threads by user_id
		var filteredThreads []*zep.Thread
		if response != nil && response.Threads != nil {
			for _, thread := range response.Threads {
				if thread.UserID != nil && *thread.UserID == input.UserID {
					filteredThreads = append(filteredThreads, thread)
				}
			}
		}

		// Format results as JSON
		resultJSON, err := transform.FormatJSON(filteredThreads)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, filteredThreads, nil
	}
}
