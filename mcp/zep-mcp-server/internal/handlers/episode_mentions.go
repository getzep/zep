package handlers

import (
	"context"
	"fmt"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleGetEpisodeMentions handles the get_episode_mentions tool
func HandleGetEpisodeMentions(client *zepclient.Client) mcp.ToolHandlerFor[GetEpisodeMentionsInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input GetEpisodeMentionsInput) (*mcp.CallToolResult, any, error) {
		mentions, err := client.Graph.Episode.GetNodesAndEdges(ctx, input.UUID)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to get episode mentions: %w", err)
		}

		resultJSON, err := transform.FormatJSON(mentions)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, mentions, nil
	}
}
