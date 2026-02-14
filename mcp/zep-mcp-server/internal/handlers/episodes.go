package handlers

import (
	"context"
	"fmt"

	"github.com/getzep/zep-go/v3/graph"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleGetEpisodes handles the get_episodes tool
func HandleGetEpisodes(client *zepclient.Client) mcp.ToolHandlerFor[GetEpisodesInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input GetEpisodesInput) (*mcp.CallToolResult, any, error) {
		// Apply defaults
		lastn := input.Lastn
		if lastn == 0 {
			lastn = 10
		}

		// Get episodes
		episodeReq := &graph.EpisodeGetByUserIDRequest{
			Lastn: &lastn,
		}

		episodes, err := client.Graph.Episode.GetByUserID(ctx, input.UserID, episodeReq)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to get episodes: %w", err)
		}

		// Format results as JSON
		resultJSON, err := transform.FormatJSON(episodes)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, episodes, nil
	}
}
