package zepadk

import (
	"context"
	"encoding/json"
	"reflect"

	zep "github.com/getzep/zep-go/v4"
	zepclient "github.com/getzep/zep-go/v4/client"
)

type SearchScope string

const (
	SearchScopeEdges           SearchScope = "edges"
	SearchScopeNodes           SearchScope = "nodes"
	SearchScopeEpisodes        SearchScope = "episodes"
	SearchScopeObservations    SearchScope = "observations"
	SearchScopeThreadSummaries SearchScope = "thread_summaries"
	SearchScopeAuto            SearchScope = "auto"
)

type SearchReranker string

const (
	SearchRerankerRRF             SearchReranker = "rrf"
	SearchRerankerMMR             SearchReranker = "mmr"
	SearchRerankerNodeDistance    SearchReranker = "node_distance"
	SearchRerankerEpisodeMentions SearchReranker = "episode_mentions"
	SearchRerankerCrossEncoder    SearchReranker = "cross_encoder"
)

type graphSearchRequest struct {
	query       string
	scope       SearchScope
	reranker    *SearchReranker
	pageSize    *int
	mmrLambda   *float64
	centerNode  *string
	bfsOrigins  []string
	filters     any
	autoFilters *zep.SearchFilters
}

func searchScopeSupported(scope SearchScope) bool {
	switch scope {
	case SearchScopeEdges, SearchScopeNodes, SearchScopeEpisodes,
		SearchScopeObservations, SearchScopeThreadSummaries, SearchScopeAuto:
		return true
	default:
		return false
	}
}

func searchRerankerSupported(reranker SearchReranker) bool {
	switch reranker {
	case SearchRerankerRRF, SearchRerankerMMR, SearchRerankerNodeDistance,
		SearchRerankerEpisodeMentions, SearchRerankerCrossEncoder:
		return true
	default:
		return false
	}
}

func filterAs[T any](filter any) *T {
	if filter == nil {
		return nil
	}
	if typed, ok := filter.(*T); ok {
		return typed
	}
	data, err := json.Marshal(filter)
	if err != nil {
		return nil
	}
	var typed T
	if json.Unmarshal(data, &typed) != nil {
		return nil
	}
	return &typed
}

func searchGraph(ctx context.Context, client *zepclient.Client, graphUUID string, req graphSearchRequest) ([]string, error) {
	if req.scope == SearchScopeAuto {
		filters := req.autoFilters
		if filters == nil {
			filters = filterAs[zep.SearchFilters](req.filters)
		}
		result, err := client.Graph.GetContext(ctx, graphUUID, &zep.GraphContextRequest{
			Query:   req.query,
			Filters: filters,
		})
		if err != nil || result == nil || result.GetContext() == nil {
			return nil, err
		}
		return []string{*result.GetContext()}, nil
	}

	var page any
	var err error
	switch req.scope {
	case SearchScopeEdges:
		filters := filterAs[zep.EdgeSearchFilters](req.filters)
		r := &zep.SearchEdgesRequest{Query: req.query, PageSize: req.pageSize, MmrLambda: req.mmrLambda, CenterNodeUUID: req.centerNode, BfsOriginNodeUUIDs: req.bfsOrigins, Filters: filters}
		if req.reranker != nil {
			v := zep.SearchEdgesRequestReranker(*req.reranker)
			r.Reranker = &v
		}
		page, err = client.Graph.SearchEdges(ctx, graphUUID, r)
	case SearchScopeNodes:
		filters := filterAs[zep.NodeSearchFilters](req.filters)
		r := &zep.SearchNodesRequest{Query: req.query, PageSize: req.pageSize, MmrLambda: req.mmrLambda, CenterNodeUUID: req.centerNode, BfsOriginNodeUUIDs: req.bfsOrigins, Filters: filters}
		if req.reranker != nil {
			v := zep.SearchNodesRequestReranker(*req.reranker)
			r.Reranker = &v
		}
		page, err = client.Graph.SearchNodes(ctx, graphUUID, r)
	case SearchScopeEpisodes:
		filters := filterAs[zep.EpisodeSearchFilters](req.filters)
		r := &zep.SearchEpisodesRequest{Query: req.query, PageSize: req.pageSize, MmrLambda: req.mmrLambda, CenterNodeUUID: req.centerNode, BfsOriginNodeUUIDs: req.bfsOrigins, Filters: filters}
		if req.reranker != nil {
			v := zep.SearchEpisodesRequestReranker(*req.reranker)
			r.Reranker = &v
		}
		page, err = client.Graph.SearchEpisodes(ctx, graphUUID, r)
	case SearchScopeObservations:
		filters := filterAs[zep.ObservationSearchFilters](req.filters)
		r := &zep.SearchObservationsRequest{Query: req.query, PageSize: req.pageSize, MmrLambda: req.mmrLambda, CenterNodeUUID: req.centerNode, BfsOriginNodeUUIDs: req.bfsOrigins, Filters: filters}
		if req.reranker != nil {
			v := zep.SearchObservationsRequestReranker(*req.reranker)
			r.Reranker = &v
		}
		page, err = client.Graph.SearchObservations(ctx, graphUUID, r)
	case SearchScopeThreadSummaries:
		filters := filterAs[zep.ThreadSummarySearchFilters](req.filters)
		r := &zep.SearchThreadSummariesRequest{Query: req.query, PageSize: req.pageSize, MmrLambda: req.mmrLambda, CenterNodeUUID: req.centerNode, BfsOriginNodeUUIDs: req.bfsOrigins, Filters: filters}
		if req.reranker != nil {
			v := zep.SearchThreadSummariesRequestReranker(*req.reranker)
			r.Reranker = &v
		}
		page, err = client.Graph.SearchThreadSummaries(ctx, graphUUID, r)
	default:
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return pageText(page), nil
}

func pageText(page any) []string {
	v := reflect.ValueOf(page)
	if !v.IsValid() || v.IsNil() {
		return nil
	}
	results := v.Elem().FieldByName("Results")
	if !results.IsValid() {
		return nil
	}
	out := make([]string, 0, results.Len())
	for i := 0; i < results.Len(); i++ {
		item := results.Index(i)
		text := fieldText(item, "Fact")
		if text == "" {
			text = fieldText(item, "Content")
		}
		if text == "" {
			name, summary := fieldText(item, "Name"), fieldText(item, "Summary")
			if name != "" && summary != "" {
				text = name + ": " + summary
			} else if name != "" {
				text = name
			} else {
				text = summary
			}
		}
		if text != "" {
			out = append(out, text)
		}
	}
	return out
}

func fieldText(v reflect.Value, name string) string {
	for v.IsValid() && (v.Kind() == reflect.Interface || v.Kind() == reflect.Pointer) {
		if v.IsNil() {
			return ""
		}
		v = v.Elem()
	}
	if !v.IsValid() || v.Kind() != reflect.Struct {
		return ""
	}
	f := v.FieldByName(name)
	for f.IsValid() && (f.Kind() == reflect.Interface || f.Kind() == reflect.Pointer) {
		if f.IsNil() {
			return ""
		}
		f = f.Elem()
	}
	if f.IsValid() && f.Kind() == reflect.String {
		return f.String()
	}
	return ""
}
