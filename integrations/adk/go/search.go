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

// SearchReranker selects the result ordering algorithm of a Zep graph search.
// Zep v4 has one reranker type for each search scope, so this package defines
// one set of values and converts it for the scope of each search.
type SearchReranker string

// Supported graph search rerankers.
const (
	// SearchRerankerRRF uses reciprocal rank fusion.
	SearchRerankerRRF SearchReranker = "rrf"
	// SearchRerankerMMR uses maximal marginal relevance.
	SearchRerankerMMR SearchReranker = "mmr"
	// SearchRerankerNodeDistance orders results by distance from a center node.
	SearchRerankerNodeDistance SearchReranker = "node_distance"
	// SearchRerankerEpisodeMentions orders results by episode mentions. Zep
	// requires the EpisodeUUIDs search filter for this reranker, so the graph
	// search tool does not offer it to the model. Set it with
	// WithToolReranker and WithToolSearchFilters.
	SearchRerankerEpisodeMentions SearchReranker = "episode_mentions"
	// SearchRerankerCrossEncoder orders results with a cross-encoder.
	SearchRerankerCrossEncoder SearchReranker = "cross_encoder"
)

// newModelSearchRerankerFromString converts a model-supplied value into a
// [SearchReranker]. It reports an error for a value that the model cannot
// select. The model cannot select [SearchRerankerEpisodeMentions], because
// Zep requires episode UUID filters for that reranker.
func newModelSearchRerankerFromString(s string) (SearchReranker, error) {
	switch r := SearchReranker(s); r {
	case SearchRerankerRRF,
		SearchRerankerMMR,
		SearchRerankerNodeDistance,
		SearchRerankerCrossEncoder:
		return r, nil
	default:
		return "", errUnsupportedReranker
	}
}

// maxSearchPageSize is the largest page_size that a Zep v4 search accepts.
const maxSearchPageSize = 50

// searchParams holds the scope-independent parameters of a graph search.
// searchGraph converts them to the request type of the selected scope.
type searchParams struct {
	Query              string
	Reranker           *SearchReranker
	MmrLambda          *float64
	CenterNodeUUID     *string
	BfsOriginNodeUUIDs []string
	Filters            *zep.SearchFilters
}

// searchPageSize returns the page_size for a search that collects limit
// results. A nil limit uses the Zep default.
func searchPageSize(limit *int) *int {
	if limit == nil {
		return nil
	}
	return zep.Int(min(*limit, maxSearchPageSize))
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
// no MMR weighting. Each Zep v4 search scope accepts only some filter keys, so
// searchGraph returns an error when params.Filters has a key that the scope
// cannot apply.
func searchGraph(ctx context.Context, api zepAPI, scope SearchScope, graphUUID string, limit *int, params *searchParams) ([]string, error) {
	pageSize := searchPageSize(limit)
	switch scope {
	case SearchScopeAuto:
		res, err := api.GetGraphContext(ctx, graphUUID, &zep.GraphContextRequest{
			Query:   params.Query,
			Filters: params.Filters,
		})
		if err != nil {
			return nil, err
		}
		if res != nil && deref(res.Context) != "" {
			return []string{*res.Context}, nil
		}
		return nil, nil

	case SearchScopeNodes:
		filters, err := nodeSearchFilters(params.Filters)
		if err != nil {
			return nil, err
		}
		req := &zep.SearchNodesRequest{
			PageSize:           pageSize,
			Query:              params.Query,
			BfsOriginNodeUUIDs: params.BfsOriginNodeUUIDs,
			CenterNodeUUID:     params.CenterNodeUUID,
			Filters:            filters,
			MmrLambda:          params.MmrLambda,
		}
		if params.Reranker != nil {
			req.Reranker = zep.SearchNodesRequestReranker(*params.Reranker).Ptr()
		}
		nodes, err := api.SearchNodes(ctx, graphUUID, req, limit)
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
		filters, err := episodeSearchFilters(params.Filters)
		if err != nil {
			return nil, err
		}
		req := &zep.SearchEpisodesRequest{
			PageSize:           pageSize,
			Query:              params.Query,
			BfsOriginNodeUUIDs: params.BfsOriginNodeUUIDs,
			CenterNodeUUID:     params.CenterNodeUUID,
			Filters:            filters,
			MmrLambda:          params.MmrLambda,
		}
		if params.Reranker != nil {
			req.Reranker = zep.SearchEpisodesRequestReranker(*params.Reranker).Ptr()
		}
		episodes, err := api.SearchEpisodes(ctx, graphUUID, req, limit)
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
		if !filtersEmpty(params.Filters) {
			return nil, errUnsupportedFilter
		}
		req := &zep.SearchObservationsRequest{
			PageSize:           pageSize,
			Query:              params.Query,
			BfsOriginNodeUUIDs: params.BfsOriginNodeUUIDs,
			CenterNodeUUID:     params.CenterNodeUUID,
			MmrLambda:          params.MmrLambda,
		}
		if params.Reranker != nil {
			req.Reranker = zep.SearchObservationsRequestReranker(*params.Reranker).Ptr()
		}
		observations, err := api.SearchObservations(ctx, graphUUID, req, limit)
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
		filters, err := threadSummarySearchFilters(params.Filters)
		if err != nil {
			return nil, err
		}
		req := &zep.SearchThreadSummariesRequest{
			PageSize:           pageSize,
			Query:              params.Query,
			BfsOriginNodeUUIDs: params.BfsOriginNodeUUIDs,
			CenterNodeUUID:     params.CenterNodeUUID,
			Filters:            filters,
			MmrLambda:          params.MmrLambda,
		}
		if params.Reranker != nil {
			req.Reranker = zep.SearchThreadSummariesRequestReranker(*params.Reranker).Ptr()
		}
		summaries, err := api.SearchThreadSummaries(ctx, graphUUID, req, limit)
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
		filters, err := edgeSearchFilters(params.Filters)
		if err != nil {
			return nil, err
		}
		req := &zep.SearchEdgesRequest{
			PageSize:           pageSize,
			Query:              params.Query,
			BfsOriginNodeUUIDs: params.BfsOriginNodeUUIDs,
			CenterNodeUUID:     params.CenterNodeUUID,
			Filters:            filters,
			MmrLambda:          params.MmrLambda,
		}
		if params.Reranker != nil {
			req.Reranker = zep.SearchEdgesRequestReranker(*params.Reranker).Ptr()
		}
		edges, err := api.SearchEdges(ctx, graphUUID, req, limit)
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

// filtersEmpty reports whether f sets no filter key.
func filtersEmpty(f *zep.SearchFilters) bool {
	return f == nil || (len(f.ConnectedNodeUUIDs) == 0 &&
		f.ContentPolicyViolated == nil &&
		f.DateFilters == nil &&
		len(f.EdgeTypes) == 0 &&
		len(f.EdgeUUIDs) == 0 &&
		len(f.EpisodeUUIDs) == 0 &&
		len(f.ExcludeEdgeTypes) == 0 &&
		len(f.ExcludeNodeLabels) == 0 &&
		f.MetadataFilters == nil &&
		len(f.NodeLabels) == 0 &&
		len(f.PropertyFilters) == 0 &&
		len(f.SourceNodeUUIDs) == 0 &&
		len(f.TargetNodeUUIDs) == 0)
}

// edgeSearchFilters converts f to the filters of an edge search.
func edgeSearchFilters(f *zep.SearchFilters) (*zep.EdgeSearchFilters, error) {
	if filtersEmpty(f) {
		return nil, nil
	}
	if f.ContentPolicyViolated != nil || len(f.ExcludeNodeLabels) > 0 || len(f.NodeLabels) > 0 {
		return nil, errUnsupportedFilter
	}
	return &zep.EdgeSearchFilters{
		ConnectedNodeUUIDs: f.ConnectedNodeUUIDs,
		DateFilters:        f.DateFilters,
		EdgeTypes:          f.EdgeTypes,
		EdgeUUIDs:          f.EdgeUUIDs,
		EpisodeUUIDs:       f.EpisodeUUIDs,
		ExcludeEdgeTypes:   f.ExcludeEdgeTypes,
		MetadataFilters:    f.MetadataFilters,
		PropertyFilters:    f.PropertyFilters,
		SourceNodeUUIDs:    f.SourceNodeUUIDs,
		TargetNodeUUIDs:    f.TargetNodeUUIDs,
	}, nil
}

// nodeSearchFilters converts f to the filters of a node search.
func nodeSearchFilters(f *zep.SearchFilters) (*zep.NodeSearchFilters, error) {
	if filtersEmpty(f) {
		return nil, nil
	}
	if len(f.ConnectedNodeUUIDs) > 0 || f.ContentPolicyViolated != nil || f.DateFilters != nil ||
		len(f.EdgeTypes) > 0 || len(f.EdgeUUIDs) > 0 || len(f.ExcludeEdgeTypes) > 0 ||
		len(f.SourceNodeUUIDs) > 0 || len(f.TargetNodeUUIDs) > 0 {
		return nil, errUnsupportedFilter
	}
	return &zep.NodeSearchFilters{
		EpisodeUUIDs:      f.EpisodeUUIDs,
		ExcludeNodeLabels: f.ExcludeNodeLabels,
		MetadataFilters:   f.MetadataFilters,
		NodeLabels:        f.NodeLabels,
		PropertyFilters:   f.PropertyFilters,
	}, nil
}

// episodeSearchFilters converts f to the filters of an episode search.
func episodeSearchFilters(f *zep.SearchFilters) (*zep.EpisodeSearchFilters, error) {
	if filtersEmpty(f) {
		return nil, nil
	}
	if !filtersEmpty(&zep.SearchFilters{
		ConnectedNodeUUIDs: f.ConnectedNodeUUIDs,
		DateFilters:        f.DateFilters,
		EdgeTypes:          f.EdgeTypes,
		EdgeUUIDs:          f.EdgeUUIDs,
		EpisodeUUIDs:       f.EpisodeUUIDs,
		ExcludeEdgeTypes:   f.ExcludeEdgeTypes,
		ExcludeNodeLabels:  f.ExcludeNodeLabels,
		NodeLabels:         f.NodeLabels,
		PropertyFilters:    f.PropertyFilters,
		SourceNodeUUIDs:    f.SourceNodeUUIDs,
		TargetNodeUUIDs:    f.TargetNodeUUIDs,
	}) {
		return nil, errUnsupportedFilter
	}
	return &zep.EpisodeSearchFilters{
		ContentPolicyViolated: f.ContentPolicyViolated,
		MetadataFilters:       f.MetadataFilters,
	}, nil
}

// threadSummarySearchFilters converts f to the filters of a thread summary
// search.
func threadSummarySearchFilters(f *zep.SearchFilters) (*zep.ThreadSummarySearchFilters, error) {
	if filtersEmpty(f) {
		return nil, nil
	}
	if !filtersEmpty(&zep.SearchFilters{
		ConnectedNodeUUIDs:    f.ConnectedNodeUUIDs,
		ContentPolicyViolated: f.ContentPolicyViolated,
		DateFilters:           f.DateFilters,
		EdgeTypes:             f.EdgeTypes,
		EdgeUUIDs:             f.EdgeUUIDs,
		EpisodeUUIDs:          f.EpisodeUUIDs,
		ExcludeEdgeTypes:      f.ExcludeEdgeTypes,
		ExcludeNodeLabels:     f.ExcludeNodeLabels,
		NodeLabels:            f.NodeLabels,
		PropertyFilters:       f.PropertyFilters,
		SourceNodeUUIDs:       f.SourceNodeUUIDs,
		TargetNodeUUIDs:       f.TargetNodeUUIDs,
	}) {
		return nil, errUnsupportedFilter
	}
	return &zep.ThreadSummarySearchFilters{MetadataFilters: f.MetadataFilters}, nil
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
