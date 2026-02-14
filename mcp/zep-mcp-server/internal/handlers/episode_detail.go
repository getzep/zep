package handlers

import (
	"context"
	"fmt"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleGetEpisode handles the get_episode tool
func HandleGetEpisode(client *zepclient.Client) mcp.ToolHandlerFor[GetEpisodeInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input GetEpisodeInput) (*mcp.CallToolResult, any, error) {
		episode, err := client.Graph.Episode.Get(ctx, input.UUID)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to get episode: %w", err)
		}

		resultJSON, err := transform.FormatJSON(episode)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, episode, nil
	}
}
