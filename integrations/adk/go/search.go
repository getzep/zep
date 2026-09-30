package zepadk

import (
	"context"

	zep "github.com/getzep/zep-go/v4"
)

// SearchScope selects which kind of graph data a Zep search returns. Zep v4
// replaced the single v3 graph search method with one method for each scope,
// and it no longer exports a scope enum, so this package defines the scope
// values that it maps into textual results.
type SearchScope string

// Supported graph search scopes.
const (
	// SearchScopeEdges returns facts and relationships.
	SearchScopeEdges SearchScope = "edges"
	// SearchScopeNodes returns entities and their summaries.
	SearchScopeNodes SearchScope = "nodes"
	// SearchScopeEpisodes returns raw ingested content.
	SearchScopeEpisodes SearchScope = "episodes"
	// SearchScopeObservations returns derived memories.
	SearchScopeObservations SearchScope = "observations"
	// SearchScopeThreadSummaries returns incremental thread summaries.
	SearchScopeThreadSummaries SearchScope = "thread_summaries"
	// SearchScopeAuto returns the assembled context block, which Zep v4
	// serves from Graph.GetContext.
	SearchScopeAuto SearchScope = "auto"
)

// searchScopeSupported reports whether scope is one this package knows how to
// map into textual results. Unsupported scopes are rejected loudly rather than
// silently returning nothing.
func searchScopeSupported(scope SearchScope) bool {
	switch scope {
	case SearchScopeEdges,
		SearchScopeNodes,
		SearchScopeEpisodes,
		SearchScopeObservations,
		SearchScopeThreadSummaries,
		SearchScopeAuto:
		return true
	default:
		return false
	}
}

// newSearchScopeFromString converts a model-supplied or caller-supplied value
// into a [SearchScope]. It reports an error for an unsupported value.
func newSearchScopeFromString(s string) (SearchScope, error) {
	scope := SearchScope(s)
	if !searchScopeSupported(scope) {
		return "", errUnsupportedScope
	}
	return scope, nil
}

// searchGraph runs the Zep v4 graph search method for scope against the graph
// identified by graphUUID, and maps every result to text:
//
//   - edges            -> each edge's fact
//   - nodes            -> "name: summary" (or the populated half)
//   - episodes         -> each episode's content
//   - observations     -> "name: summary" (or the populated half)
//   - thread_summaries -> each summary's text
//   - auto             -> the assembled context block from Graph.GetContext
//
// Empty entries are skipped. The auto scope uses only the query and the
// filters, because Graph.GetContext accepts no reranker, no center node, and
// no MMR weighting.
func searchGraph(ctx context.Context, api zepAPI, scope SearchScope, graphUUID string, limit *int, body *zep.SearchRequest) ([]string, error) {
	switch scope {
	case SearchScopeAuto:
		res, err := api.GetGraphContext(ctx, graphUUID, &zep.GraphContextRequest{
			Query:   body.Query,
			Filters: body.Filters,
		})
		if err != nil {
			return nil, err
		}
		if res != nil && deref(res.Context) != "" {
			return []string{*res.Context}, nil
		}
		return nil, nil

	case SearchScopeNodes:
		nodes, err := api.SearchNodes(ctx, graphUUID, &zep.GraphSearchNodesRequest{Limit: limit, Body: body})
		if err != nil {
			return nil, err
		}
		var out []string
		for _, node := range nodes {
			if node == nil {
				continue
			}
			if text := nameSummaryText(deref(node.Name), deref(node.Summary)); text != "" {
				out = append(out, text)
			}
		}
		return out, nil

	case SearchScopeEpisodes:
		episodes, err := api.SearchEpisodes(ctx, graphUUID, &zep.GraphSearchEpisodesRequest{Limit: limit, Body: body})
		if err != nil {
			return nil, err
		}
		var out []string
		for _, episode := range episodes {
			if episode == nil {
				continue
			}
			if content := deref(episode.Content); content != "" {
				out = append(out, content)
			}
		}
		return out, nil

	case SearchScopeObservations:
		observations, err := api.SearchObservations(ctx, graphUUID, &zep.GraphSearchObservationsRequest{Limit: limit, Body: body})
		if err != nil {
			return nil, err
		}
		var out []string
		for _, observation := range observations {
			if observation == nil {
				continue
			}
			if text := nameSummaryText(deref(observation.Name), deref(observation.Summary)); text != "" {
				out = append(out, text)
			}
		}
		return out, nil

	case SearchScopeThreadSummaries:
		summaries, err := api.SearchThreadSummaries(ctx, graphUUID, &zep.GraphSearchThreadSummariesRequest{Limit: limit, Body: body})
		if err != nil {
			return nil, err
		}
		var out []string
		for _, summary := range summaries {
			if summary == nil {
				continue
			}
			if text := deref(summary.Summary); text != "" {
				out = append(out, text)
			}
		}
		return out, nil

	default:
		edges, err := api.SearchEdges(ctx, graphUUID, &zep.GraphSearchEdgesRequest{Limit: limit, Body: body})
		if err != nil {
			return nil, err
		}
		var out []string
		for _, edge := range edges {
			if edge == nil {
				continue
			}
			if fact := deref(edge.Fact); fact != "" {
				out = append(out, fact)
			}
		}
		return out, nil
	}
}

// nameSummaryText joins a name and summary as "name: summary", returning just
// the populated half when one is empty and "" when both are.
func nameSummaryText(name, summary string) string {
	switch {
	case name != "" && summary != "":
		return name + ": " + summary
	case name != "":
		return name
	default:
		return summary
	}
}
