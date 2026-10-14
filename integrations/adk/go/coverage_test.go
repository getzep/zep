package zepadk

import (
	"context"
	"errors"
	"reflect"
	"testing"

	zep "github.com/getzep/zep-go/v4"
	"google.golang.org/adk/memory"
	"google.golang.org/adk/model"
	"google.golang.org/genai"
)

func TestBeforeCallbackDegradesWhenPersistenceFails(t *testing.T) {
	api := &fakeZepAPI{addErr: errors.New("offline")}
	callback := newBeforeModelCallback(nil, api,
		WithUserUUID("user-uuid"),
		WithThreadUUID("thread-uuid"),
		WithLogger(discardLogger()))
	cc := newFakeCallbackContext(genai.NewContentFromText("hello", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{cc.content}}

	if _, err := callback(cc, req); err != nil {
		t.Fatalf("callback error = %v, want nil", err)
	}
	if req.Config != nil && req.Config.SystemInstruction != nil {
		if got := LastUserText(req.Config.SystemInstruction); got != "" {
			t.Fatalf("injected context after persistence error = %q, want empty", got)
		}
	}
	if len(api.added) != 1 {
		t.Fatalf("persisted messages = %d, want 1 attempted message", len(api.added))
	}
}

func TestBeforeCallbackContextBuilderErrorStillPersists(t *testing.T) {
	api := &fakeZepAPI{}
	callback := newBeforeModelCallback(nil, api,
		WithUserUUID("user-uuid"),
		WithThreadUUID("thread-uuid"),
		WithContextBuilder(func(context.Context, ContextInput) (string, error) {
			return "", errors.New("builder failed")
		}),
		WithLogger(discardLogger()))
	cc := newFakeCallbackContext(genai.NewContentFromText("hello", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{cc.content}}

	if _, err := callback(cc, req); err != nil {
		t.Fatalf("callback error = %v, want nil", err)
	}
	if api.addThreadUUID != "thread-uuid" || len(api.added) != 1 {
		t.Fatalf("persisted thread=%q messages=%d", api.addThreadUUID, len(api.added))
	}
	if req.Config != nil && req.Config.SystemInstruction != nil {
		if got := LastUserText(req.Config.SystemInstruction); got != "" {
			t.Fatalf("injected context after builder error = %q, want empty", got)
		}
	}
}

func TestContextBuilderSkipsReturnContextAndUsesCustomTemplate(t *testing.T) {
	api := &fakeZepAPI{}
	callback := newBeforeModelCallback(nil, api,
		WithUserUUID("user-uuid"),
		WithThreadUUID("thread-uuid"),
		WithContextTemplate("Facts: {context} / 100%"),
		WithContextBuilder(func(context.Context, ContextInput) (string, error) {
			return "{literal}", nil
		}),
		WithLogger(discardLogger()))
	cc := newFakeCallbackContext(genai.NewContentFromText("hello", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{cc.content}}

	if _, err := callback(cc, req); err != nil {
		t.Fatalf("callback error = %v, want nil", err)
	}
	if api.addCalls != 1 || api.lastAddReq.ReturnContext != nil {
		t.Fatalf("persist calls=%d ReturnContext=%v, want one call without context", api.addCalls, api.lastAddReq.ReturnContext)
	}
	if got := LastUserText(req.Config.SystemInstruction); got != "Facts: {literal} / 100%" {
		t.Fatalf("system instruction = %q", got)
	}
}

func TestContextBuilderEmptyOrPanicsWithoutBlockingPersistence(t *testing.T) {
	for _, tc := range []struct {
		name    string
		builder ContextBuilder
	}{
		{"empty", func(context.Context, ContextInput) (string, error) { return "", nil }},
		{"panic", func(context.Context, ContextInput) (string, error) { panic("builder panic") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			api := &fakeZepAPI{}
			callback := newBeforeModelCallback(nil, api,
				WithUserUUID("user-uuid"),
				WithThreadUUID("thread-uuid"),
				WithContextBuilder(tc.builder),
				WithLogger(discardLogger()))
			cc := newFakeCallbackContext(genai.NewContentFromText("hello", genai.RoleUser))
			req := &model.LLMRequest{Contents: []*genai.Content{cc.content}}

			if _, err := callback(cc, req); err != nil {
				t.Fatalf("callback error = %v, want nil", err)
			}
			if api.addCalls != 1 {
				t.Fatalf("persist calls = %d, want 1", api.addCalls)
			}
			if req.Config != nil {
				t.Fatalf("request config = %#v, want no context injection", req.Config)
			}
		})
	}
}

func TestAfterCallbackDegradesWhenPersistenceFails(t *testing.T) {
	api := &fakeZepAPI{addErr: errors.New("offline")}
	callback := newAfterModelCallback(api,
		WithAfterThreadUUID("thread-uuid"),
		WithAfterLogger(discardLogger()))
	cc := newFakeCallbackContext(nil)
	response := &model.LLMResponse{Content: genai.NewContentFromText("answer", genai.RoleModel)}

	if _, err := callback(cc, response, nil); err != nil {
		t.Fatalf("callback error = %v, want nil", err)
	}
	if len(api.added) != 1 {
		t.Fatalf("persisted messages = %d, want 1 attempted message", len(api.added))
	}
}

func TestAfterCallbackSkipsErrorPartialAndToolResponses(t *testing.T) {
	toolCall := &genai.Content{Parts: []*genai.Part{
		genai.NewPartFromFunctionCall("search_memory", map[string]any{"query": "x"}),
	}}
	for _, tc := range []struct {
		name string
		resp *model.LLMResponse
		err  error
	}{
		{"error", &model.LLMResponse{Content: genai.NewContentFromText("partial", genai.RoleModel)}, errors.New("model error")},
		{"nil response", nil, nil},
		{"partial response", &model.LLMResponse{Partial: true, Content: genai.NewContentFromText("partial", genai.RoleModel)}, nil},
		{"tool call", &model.LLMResponse{Content: toolCall}, nil},
		{"empty", &model.LLMResponse{Content: &genai.Content{}}, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			api := &fakeZepAPI{}
			callback := newAfterModelCallback(api, WithAfterThreadUUID("thread-uuid"))
			cc := newFakeCallbackContext(nil)
			if _, err := callback(cc, tc.resp, tc.err); err != nil {
				t.Fatalf("callback error = %v, want nil", err)
			}
			if api.addCalls != 0 {
				t.Fatalf("persist calls = %d, want 0", api.addCalls)
			}
		})
	}
}

func TestGraphSearchSchemaExposesAllParametersByDefault(t *testing.T) {
	schema := buildSearchInputSchema(&graphSearchToolOptions{})
	for _, name := range []string{"query", "scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"} {
		if _, ok := schema.Properties[name]; !ok {
			t.Errorf("schema does not expose %q", name)
		}
	}
}

func TestGraphSearchSchemaHidesPinnedAndHiddenParameters(t *testing.T) {
	cfg := resolveGraphSearchToolOptions(
		WithToolSearchScope(SearchScopeNodes),
		WithToolSearchLimit(5),
		WithToolReranker(SearchRerankerMMR),
		WithToolMMRLambda(0.4),
		WithToolCenterNodeUUID("center"),
		WithHiddenParams(SearchParamReranker),
	)
	schema := buildSearchInputSchema(&cfg)
	for _, name := range []string{"scope", "limit", "reranker", "mmr_lambda", "center_node_uuid"} {
		if _, ok := schema.Properties[name]; ok {
			t.Errorf("schema exposes configured parameter %q", name)
		}
	}
	if _, ok := schema.Properties["query"]; !ok {
		t.Fatal("schema does not expose query")
	}
}

func TestGraphSearchToolAppliesPinnedAndExposedParameters(t *testing.T) {
	api := &fakeZepAPI{searchResults: []string{"fact"}}
	handler := newGraphSearchHandler(api,
		WithGraphUUID("graph-uuid"),
		WithToolSearchScope(SearchScopeNodes),
		WithToolSearchLimit(7),
		WithToolReranker(SearchRerankerMMR),
		WithToolMMRLambda(0.25),
		WithToolCenterNodeUUID("center-uuid"))
	lambda := 0.75
	center := "model-center"

	result, err := handler(nil, SearchArgs{
		Query:          "work",
		MMRLambda:      &lambda,
		CenterNodeUUID: &center,
	})
	if err != nil {
		t.Fatalf("search error = %v", err)
	}
	if !reflect.DeepEqual(result.Facts, []string{"fact"}) {
		t.Fatalf("facts = %#v", result.Facts)
	}
	req := api.searchRequest
	if req.scope != SearchScopeNodes || req.pageSize == nil || *req.pageSize != 7 {
		t.Fatalf("scope/limit = %q/%v", req.scope, req.pageSize)
	}
	if req.reranker == nil || *req.reranker != SearchRerankerMMR {
		t.Fatalf("reranker = %v", req.reranker)
	}
	if req.mmrLambda == nil || *req.mmrLambda != 0.25 {
		t.Fatalf("mmr_lambda = %v", req.mmrLambda)
	}
	if req.centerNode == nil || *req.centerNode != "center-uuid" {
		t.Fatalf("center_node_uuid = %v", req.centerNode)
	}
}

func TestGraphSearchToolHiddenParametersAreOmitted(t *testing.T) {
	api := &fakeZepAPI{}
	handler := newGraphSearchHandler(api,
		WithGraphUUID("graph-uuid"),
		WithHiddenParams(
			SearchParamScope,
			SearchParamReranker,
			SearchParamLimit,
			SearchParamMMRLambda,
			SearchParamCenterNodeUUID,
		))
	scope, reranker, limit, lambda, center := "nodes", "mmr", 4, 0.5, "center"

	if _, err := handler(nil, SearchArgs{
		Query:          "work",
		Scope:          &scope,
		Reranker:       &reranker,
		Limit:          &limit,
		MMRLambda:      &lambda,
		CenterNodeUUID: &center,
	}); err != nil {
		t.Fatalf("search error = %v", err)
	}
	req := api.searchRequest
	if req.scope != SearchScopeEdges || req.reranker != nil || req.pageSize != nil ||
		req.mmrLambda != nil || req.centerNode != nil {
		t.Fatalf("hidden parameters reached the request: %#v", req)
	}
}

func TestGraphSearchToolPassesTypedFiltersForEachScope(t *testing.T) {
	cases := []struct {
		name   string
		scope  SearchScope
		filter any
		option GraphSearchToolOption
	}{
		{"edges", SearchScopeEdges, &zep.EdgeSearchFilters{}, func() GraphSearchToolOption {
			filters := &zep.EdgeSearchFilters{}
			return WithToolEdgeSearchFilters(filters)
		}()},
		{"nodes", SearchScopeNodes, &zep.NodeSearchFilters{}, func() GraphSearchToolOption {
			filters := &zep.NodeSearchFilters{}
			return WithToolNodeSearchFilters(filters)
		}()},
		{"episodes", SearchScopeEpisodes, &zep.EpisodeSearchFilters{}, func() GraphSearchToolOption {
			filters := &zep.EpisodeSearchFilters{}
			return WithToolEpisodeSearchFilters(filters)
		}()},
		{"observations", SearchScopeObservations, &zep.ObservationSearchFilters{}, func() GraphSearchToolOption {
			filters := &zep.ObservationSearchFilters{}
			return WithToolObservationSearchFilters(filters)
		}()},
		{"thread summaries", SearchScopeThreadSummaries, &zep.ThreadSummarySearchFilters{}, func() GraphSearchToolOption {
			filters := &zep.ThreadSummarySearchFilters{}
			return WithToolThreadSummarySearchFilters(filters)
		}()},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			api := &fakeZepAPI{}
			handler := newGraphSearchHandler(api,
				WithGraphUUID("graph-uuid"),
				WithToolSearchScope(tc.scope),
				tc.option)
			if _, err := handler(nil, SearchArgs{Query: "work"}); err != nil {
				t.Fatalf("search error = %v", err)
			}
			if api.searchRequest.scope != tc.scope {
				t.Fatalf("scope = %q, want %q", api.searchRequest.scope, tc.scope)
			}
			if reflect.TypeOf(api.searchRequest.filters) != reflect.TypeOf(tc.filter) {
				t.Fatalf("filter type = %T, want %T", api.searchRequest.filters, tc.filter)
			}
		})
	}
}

func TestMemoryServiceMapsSearchResultsToEntries(t *testing.T) {
	api := &fakeZepAPI{searchResults: []string{"first fact", "second fact"}}
	service := &memoryService{
		api: api, graphUUID: "graph-uuid",
		search: graphSearchRequest{scope: SearchScopeEdges},
		logger: discardLogger(),
	}
	response, err := service.SearchMemory(context.Background(), &memory.SearchRequest{Query: "work"})
	if err != nil {
		t.Fatalf("SearchMemory error = %v", err)
	}
	if len(response.Memories) != 2 {
		t.Fatalf("memory count = %d, want 2", len(response.Memories))
	}
	for i, want := range []string{"first fact", "second fact"} {
		if got := response.Memories[i].Content.Parts[0].Text; got != want {
			t.Errorf("memory %d = %q, want %q", i, got, want)
		}
	}
	if api.searchRequest.query != "work" {
		t.Fatalf("search query = %q, want work", api.searchRequest.query)
	}
}

func TestPageTextMapsSearchResultFields(t *testing.T) {
	type result struct {
		Fact    *string
		Content *string
		Name    *string
		Summary *string
	}
	type page struct{ Results []*result }
	fact, content, name, summary := "fact", "episode", "node", "summary"
	got := pageText(&page{Results: []*result{
		{Fact: &fact},
		{Content: &content},
		{Name: &name, Summary: &summary},
		{Summary: &summary},
		{},
	}})
	want := []string{"fact", "episode", "node: summary", "summary"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("pageText = %#v, want %#v", got, want)
	}
}
