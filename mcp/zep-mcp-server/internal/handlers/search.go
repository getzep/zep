package handlers

import (
	"context"
	"fmt"

	zep "github.com/getzep/zep-go/v3"
	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/transform"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// HandleSearchGraph handles the search_graph tool using the new MCP SDK signature
func HandleSearchGraph(client *zepclient.Client) mcp.ToolHandlerFor[SearchGraphInput, any] {
	return func(ctx context.Context, req *mcp.CallToolRequest, input SearchGraphInput) (*mcp.CallToolResult, any, error) {
		// Apply defaults
		if input.Scope == "" {
			input.Scope = "edges"
		}
		if input.Limit == 0 {
			input.Limit = 10
		}

		// Build search request
		searchReq := &zep.GraphSearchQuery{
			UserID: &input.UserID,
			Query:  input.Query,
			Limit:  &input.Limit,
		}

		// Set scope as GraphSearchScope type
		searchScope, err := zep.NewGraphSearchScopeFromString(input.Scope)
		if err != nil {
			return nil, nil, fmt.Errorf("invalid search scope: %w", err)
		}
		searchReq.Scope = &searchScope

		if input.Reranker != "" {
			rerankerType := zep.Reranker(input.Reranker)
			searchReq.Reranker = &rerankerType
		}

		if input.MinFactRating > 0.0 {
			searchReq.MinFactRating = &input.MinFactRating
		}

		if input.MmrLambda > 0.0 {
			searchReq.MmrLambda = &input.MmrLambda
		}

		if input.CenterNodeUUID != "" {
			searchReq.CenterNodeUUID = &input.CenterNodeUUID
		}

		if len(input.NodeLabels) > 0 || len(input.EdgeTypes) > 0 {
			filters := &zep.SearchFilters{}
			if len(input.NodeLabels) > 0 {
				filters.NodeLabels = input.NodeLabels
			}
			if len(input.EdgeTypes) > 0 {
				filters.EdgeTypes = input.EdgeTypes
			}
			searchReq.SearchFilters = filters
		}

		// Execute search
		results, err := client.Graph.Search(ctx, searchReq)
		if err != nil {
			return nil, nil, fmt.Errorf("graph search failed: %w", err)
		}

		// Format results as JSON
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
