package handlers

import (
	"context"
	"fmt"

	zep "github.com/getzep/zep-go/v3"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleDetectPatterns handles the detect_patterns tool.
func HandleDetectPatterns(client *zepclient.Client) mcp.ToolHandlerFor[DetectPatternsInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input DetectPatternsInput) (*mcp.CallToolResult, any, error) {
		detectReq, err := buildDetectPatternsRequest(input)
		if err != nil {
			return nil, nil, err
		}

		results, err := client.Graph.DetectPatterns(ctx, detectReq)
		if err != nil {
			return nil, nil, fmt.Errorf("pattern detection failed: %w", err)
		}

		resultJSON, err := transform.FormatJSON(results)
		if err != nil {
			return nil, nil, fmt.Errorf("failed to format results: %w", err)
		}

		return &mcp.CallToolResult{
			Content: []mcp.Content{
				&mcp.TextContent{
					Text: resultJSON,
				},
			},
		}, results, nil
	}
}

func buildDetectPatternsRequest(input DetectPatternsInput) (*zep.DetectPatternsRequest, error) {
	if input.UserID == "" && input.GraphID == "" {
		return nil, fmt.Errorf("either user_id or graph_id is required")
	}
	if input.UserID != "" && input.GraphID != "" {
		return nil, fmt.Errorf("provide only one of user_id or graph_id")
	}

	detectReq := &zep.DetectPatternsRequest{
		Detect:        input.Detect,
		SearchFilters: input.SearchFilters,
		Seeds:         input.Seeds,
	}

	if input.UserID != "" {
		detectReq.UserID = &input.UserID
	}
	if input.GraphID != "" {
		detectReq.GraphID = &input.GraphID
	}
	if input.Limit > 0 {
		detectReq.Limit = &input.Limit
	}
	if input.MinOccurrences > 0 {
		detectReq.MinOccurrences = &input.MinOccurrences
	}
	if input.IncludeExamples {
		detectReq.IncludeExamples = &input.IncludeExamples
	}
	if input.RecencyWeight != "" {
		recencyWeight, err := zep.NewRecencyWeightFromString(input.RecencyWeight)
		if err != nil {
			return nil, fmt.Errorf("invalid recency_weight: %w", err)
		}
		detectReq.RecencyWeight = &recencyWeight
	}

	return detectReq, nil
}
