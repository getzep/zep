package zepadk

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strings"
	"testing"

	zep "github.com/getzep/zep-go/v3"

	"google.golang.org/adk/agent"
	"google.golang.org/adk/memory"
	"google.golang.org/adk/session"
	"google.golang.org/adk/tool/functiontool"
	"google.golang.org/adk/tool/toolconfirmation"
	"google.golang.org/genai"

	"github.com/google/jsonschema-go/jsonschema"
)

// declaredTool is satisfied by the functiontool.New return value; asserting
// against it lets tests inspect the *genai.FunctionDeclaration that ADK will
// actually send to the model, rather than trusting that jsonschema-go content
// survives untouched.
type declaredTool interface {
	Declaration() *genai.FunctionDeclaration
}

// TestADKSchemaConversionCarriesEnumsAndDefaults is the brief's §1
// risk-verification test: it constructs a minimal functiontool with an
// explicit InputSchema containing an enum string property and a numeric
// property, and confirms that ADK's declaration carries the enum values and
// default through to the schema the model sees. If this ever regresses (e.g.
// a future ADK version converts to genai.Schema and drops enum/default),
// this test fails loudly instead of the tool silently exposing an
// unconstrained parameter.
func TestADKSchemaConversionCarriesEnumsAndDefaults(t *testing.T) {
	type verifyArgs struct {
		Query string  `json:"query"`
		Scope *string `json:"scope,omitempty"`
		Limit *int    `json:"limit,omitempty"`
	}
	type verifyResult struct {
		OK bool `json:"ok"`
	}

	defaultScope := json.RawMessage(`"edges"`)
	schema := &jsonschema.Schema{
		Type:     "object",
		Required: []string{"query"},
		Properties: map[string]*jsonschema.Schema{
			"query": {
				Type:        "string",
				Description: "Search query text",
			},
			"scope": {
				Type:        "string",
				Description: "What to search for",
				Enum:        []any{"edges", "nodes", "episodes", "observations", "thread_summaries", "auto"},
				Default:     defaultScope,
			},
			"limit": {
				Type:        "integer",
				Description: "Maximum number of results",
			},
		},
	}

	handler := func(agent.ToolContext, verifyArgs) (verifyResult, error) {
		return verifyResult{OK: true}, nil
	}

	tl, err := functiontool.New(functiontool.Config{
		Name:        "verify_tool",
		Description: "verify enum/default survival",
		InputSchema: schema,
	}, handler)
	if err != nil {
		t.Fatalf("functiontool.New err = %v", err)
	}

	dt, ok := tl.(declaredTool)
	if !ok {
		t.Fatalf("tool %T does not expose Declaration()", tl)
	}
	decl := dt.Declaration()
	if decl == nil {
		t.Fatal("Declaration() = nil, want non-nil")
	}

	// The ADK/genai FunctionDeclaration carries the raw JSON schema via
	// ParametersJsonSchema rather than converting to genai.Schema, so
	// enum/default must appear verbatim once round-tripped through JSON.
	raw, err := json.Marshal(decl.ParametersJsonSchema)
	if err != nil {
		t.Fatalf("failed to marshal ParametersJsonSchema: %v", err)
	}

	var got struct {
		Properties struct {
			Scope struct {
				Enum    []string        `json:"enum"`
				Default json.RawMessage `json:"default"`
			} `json:"scope"`
			Limit struct {
				Type string `json:"type"`
			} `json:"limit"`
		} `json:"properties"`
		Required []string `json:"required"`
	}
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("failed to unmarshal declared schema: %v\nraw: %s", err, raw)
	}

	wantEnum := []string{"edges", "nodes", "episodes", "observations", "thread_summaries", "auto"}
	if len(got.Properties.Scope.Enum) != len(wantEnum) {
		t.Fatalf("scope enum = %v, want %v (raw schema: %s)", got.Properties.Scope.Enum, wantEnum, raw)
	}
	for i, v := range wantEnum {
		if got.Properties.Scope.Enum[i] != v {
			t.Fatalf("scope enum[%d] = %q, want %q (raw schema: %s)", i, got.Properties.Scope.Enum[i], v, raw)
		}
	}
	if string(got.Properties.Scope.Default) != `"edges"` {
		t.Fatalf("scope default = %s, want %q (raw schema: %s)", got.Properties.Scope.Default, `"edges"`, raw)
	}
	if got.Properties.Limit.Type != "integer" {
		t.Fatalf("limit type = %q, want integer (raw schema: %s)", got.Properties.Limit.Type, raw)
	}
	if len(got.Required) != 1 || got.Required[0] != "query" {
		t.Fatalf("required = %v, want [query] (raw schema: %s)", got.Required, raw)
	}
}

// --- NewGraphSearchTool: schema content ------------------------------------

// declaredSchemaProps decodes the properties/required/enum/default shape of a
// built tool's declaration for assertions below.
type declaredSchemaProps struct {
	Properties map[string]struct {
		Type    string          `json:"type"`
		Enum    []string        `json:"enum,omitempty"`
		Default json.RawMessage `json:"default,omitempty"`
	} `json:"properties"`
	Required []string `json:"required"`
}

func declarationSchema(t *testing.T, tl declaredTool) declaredSchemaProps {
	t.Helper()
	decl := tl.Declaration()
	if decl == nil {
		t.Fatal("Declaration() = nil")
	}
	raw, err := json.Marshal(decl.ParametersJsonSchema)
	if err != nil {
		t.Fatalf("marshal schema: %v", err)
	}
	var got declaredSchemaProps
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("unmarshal schema: %v\nraw: %s", err, raw)
	}
	return got
}

func buildSearchTool(t *testing.T, opts ...GraphSearchToolOption) declaredTool {
	t.Helper()
	tl, err := NewGraphSearchTool(nil, opts...)
	if err != nil {
		t.Fatalf("NewGraphSearchTool err = %v", err)
	}
	dt, ok := tl.(declaredTool)
	if !ok {
		t.Fatalf("tool %T does not expose Declaration()", tl)
	}
	return dt
}

func TestGraphSearchToolSchemaDefaultExposesAllFiveParams(t *testing.T) {
	schema := declarationSchema(t, buildSearchTool(t))

	if len(schema.Required) != 1 || schema.Required[0] != "query" {
		t.Fatalf("required = %v, want [query]", schema.Required)
	}

	wantParams := []string{"query", "scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"}
	for _, p := range wantParams {
		if _, ok := schema.Properties[p]; !ok {
			t.Fatalf("expected property %q in default schema, got %+v", p, schema.Properties)
		}
	}

	scope := schema.Properties["scope"]
	wantScopeEnum := []string{"edges", "nodes", "episodes", "observations", "thread_summaries", "auto"}
	if len(scope.Enum) != len(wantScopeEnum) {
		t.Fatalf("scope enum = %v, want %v", scope.Enum, wantScopeEnum)
	}
	for i, v := range wantScopeEnum {
		if scope.Enum[i] != v {
			t.Fatalf("scope enum[%d] = %q, want %q", i, scope.Enum[i], v)
		}
	}
	if string(scope.Default) != `"edges"` {
		t.Fatalf("scope default = %s, want \"edges\"", scope.Default)
	}

	reranker := schema.Properties["reranker"]
	wantRerankerEnum := []string{"rrf", "mmr", "node_distance", "episode_mentions", "cross_encoder"}
	if len(reranker.Enum) != len(wantRerankerEnum) {
		t.Fatalf("reranker enum = %v, want %v", reranker.Enum, wantRerankerEnum)
	}
	if string(reranker.Default) != `"rrf"` {
		t.Fatalf("reranker default = %s, want \"rrf\"", reranker.Default)
	}

	limit := schema.Properties["limit"]
	if string(limit.Default) != "10" {
		t.Fatalf("limit default = %s, want 10", limit.Default)
	}

	mmr := schema.Properties["mmr_lambda"]
	if len(mmr.Default) != 0 {
		t.Fatalf("mmr_lambda default = %s, want no default", mmr.Default)
	}

	center := schema.Properties["center_node_uuid"]
	if len(center.Default) != 0 {
		t.Fatalf("center_node_uuid default = %s, want no default", center.Default)
	}
}

func TestGraphSearchToolSchemaPinnedParamsAreAbsent(t *testing.T) {
	schema := declarationSchema(t, buildSearchTool(t,
		WithToolSearchScope(zep.GraphSearchScopeNodes),
		WithToolSearchLimit(5),
		WithToolReranker(zep.RerankerMmr),
		WithToolMMRLambda(0.5),
		WithToolCenterNodeUUID("node-1"),
	))

	for _, p := range []string{"scope", "reranker", "limit", "mmr_lambda", "center_node_uuid"} {
		if _, ok := schema.Properties[p]; ok {
			t.Fatalf("expected %q to be pinned and hidden from schema, got %+v", p, schema.Properties)
		}
	}
	if _, ok := schema.Properties["query"]; !ok {
		t.Fatal("expected query to remain in schema")
	}
}

func TestGraphSearchToolSchemaHiddenParamsAreAbsentAndNotPinned(t *testing.T) {
	schema := declarationSchema(t, buildSearchTool(t,
		WithHiddenParams(SearchParamScope, SearchParamMMRLambda),
	))

	if _, ok := schema.Properties["scope"]; ok {
		t.Fatal("expected scope to be hidden from schema")
	}
	if _, ok := schema.Properties["mmr_lambda"]; ok {
		t.Fatal("expected mmr_lambda to be hidden from schema")
	}
	// Not pinned, so reranker/limit/center_node_uuid remain exposed.
	for _, p := range []string{"reranker", "limit", "center_node_uuid"} {
		if _, ok := schema.Properties[p]; !ok {
			t.Fatalf("expected %q to remain exposed, got %+v", p, schema.Properties)
		}
	}
}

// --- Execution merge: pinned > model > default; hidden omitted -------------

// fakeSearchToolContext is a minimal agent.ToolContext stub sufficient to
// drive the graph search handler in isolation.
type fakeSearchToolContext struct {
	context.Context
	userID string
}

func (f fakeSearchToolContext) UserContent() *genai.Content          { return nil }
func (f fakeSearchToolContext) InvocationID() string                 { return "inv" }
func (f fakeSearchToolContext) AgentName() string                    { return "agent" }
func (f fakeSearchToolContext) ReadonlyState() session.ReadonlyState { return nil }
func (f fakeSearchToolContext) UserID() string                       { return f.userID }
func (f fakeSearchToolContext) AppName() string                      { return "app" }
func (f fakeSearchToolContext) SessionID() string                    { return "session" }
func (f fakeSearchToolContext) Branch() string                       { return "" }
func (f fakeSearchToolContext) Artifacts() agent.Artifacts           { return nil }
func (f fakeSearchToolContext) State() session.State                 { return nil }
func (f fakeSearchToolContext) FunctionCallID() string               { return "call-1" }
func (f fakeSearchToolContext) Actions() *session.EventActions       { return nil }
func (f fakeSearchToolContext) SearchMemory(context.Context, string) (*memory.SearchResponse, error) {
	return nil, nil
}
func (f fakeSearchToolContext) ToolConfirmation() *toolconfirmation.ToolConfirmation { return nil }
func (f fakeSearchToolContext) RequestConfirmation(string, any) error                { return nil }

var _ agent.ToolContext = fakeSearchToolContext{}

// searchToolFixture builds a graph search handler wired to a fakeZepAPI, so
// the merge/filters/scope logic can be exercised directly without going
// through functiontool's reflection-based argument conversion.
type searchToolFixture struct {
	api *fakeZepAPI
	run func(ctx context.Context, args SearchArgs) (SearchResult, error)
}

func newSearchToolFixture(t *testing.T, opts ...GraphSearchToolOption) *searchToolFixture {
	t.Helper()
	api := &fakeZepAPI{searchRes: &zep.GraphSearchResults{Edges: []*zep.EntityEdge{{Fact: "f"}}}}
	handler := newGraphSearchHandler(api, opts...)
	return &searchToolFixture{
		api: api,
		run: func(ctx context.Context, args SearchArgs) (SearchResult, error) {
			return handler(fakeSearchToolContext{Context: ctx, userID: "u1"}, args)
		},
	}
}

func TestGraphSearchHandlerMergePrecedence(t *testing.T) {
	t.Run("pinned beats model arg", func(t *testing.T) {
		f := newSearchToolFixture(t, WithToolSearchScope(zep.GraphSearchScopeNodes))
		modelScope := "episodes"
		if _, err := f.run(context.Background(), SearchArgs{Query: "q", Scope: &modelScope}); err != nil {
			t.Fatalf("run err = %v", err)
		}
		if f.api.lastQuery.Scope == nil || *f.api.lastQuery.Scope != zep.GraphSearchScopeNodes {
			t.Fatalf("scope = %v, want pinned nodes", f.api.lastQuery.Scope)
		}
	})

	t.Run("model arg beats default", func(t *testing.T) {
		f := newSearchToolFixture(t)
		modelScope := "episodes"
		if _, err := f.run(context.Background(), SearchArgs{Query: "q", Scope: &modelScope}); err != nil {
			t.Fatalf("run err = %v", err)
		}
		if f.api.lastQuery.Scope == nil || *f.api.lastQuery.Scope != zep.GraphSearchScopeEpisodes {
			t.Fatalf("scope = %v, want model-provided episodes", f.api.lastQuery.Scope)
		}
	})

	t.Run("default used when nothing else set", func(t *testing.T) {
		f := newSearchToolFixture(t)
		if _, err := f.run(context.Background(), SearchArgs{Query: "q"}); err != nil {
			t.Fatalf("run err = %v", err)
		}
		if f.api.lastQuery.Scope == nil || *f.api.lastQuery.Scope != zep.GraphSearchScopeEdges {
			t.Fatalf("scope = %v, want default edges", f.api.lastQuery.Scope)
		}
		if f.api.lastQuery.Reranker == nil || *f.api.lastQuery.Reranker != zep.RerankerRrf {
			t.Fatalf("reranker = %v, want default rrf", f.api.lastQuery.Reranker)
		}
		if f.api.lastQuery.Limit == nil || *f.api.lastQuery.Limit != 10 {
			t.Fatalf("limit = %v, want default 10", f.api.lastQuery.Limit)
		}
	})

	// The two invalid-enum cases below exercise defense-in-depth in the raw
	// handler: through NewGraphSearchTool, ADK validates the model's
	// arguments against the input schema (including enums) before the
	// handler runs, so an invalid value is rejected there and returned to
	// the model as a tool error. The fallback only fires when the handler
	// is invoked outside functiontool, as these tests do.
	t.Run("invalid model enum falls back to default with warning", func(t *testing.T) {
		var buf strings.Builder
		logger := slog.New(slog.NewTextHandler(&buf, nil))
		f := newSearchToolFixture(t, WithToolLogger(logger))
		badScope := "not_a_real_scope"
		if _, err := f.run(context.Background(), SearchArgs{Query: "q", Scope: &badScope}); err != nil {
			t.Fatalf("run err = %v", err)
		}
		if f.api.lastQuery.Scope == nil || *f.api.lastQuery.Scope != zep.GraphSearchScopeEdges {
			t.Fatalf("scope = %v, want fallback default edges", f.api.lastQuery.Scope)
		}
		if !strings.Contains(buf.String(), "invalid") {
			t.Fatalf("expected a warning to be logged for invalid scope, got: %s", buf.String())
		}
	})

	t.Run("invalid model reranker enum falls back to default", func(t *testing.T) {
		f := newSearchToolFixture(t, WithToolLogger(discardLogger()))
		bad := "not_a_reranker"
		if _, err := f.run(context.Background(), SearchArgs{Query: "q", Reranker: &bad}); err != nil {
			t.Fatalf("run err = %v", err)
		}
		if f.api.lastQuery.Reranker == nil || *f.api.lastQuery.Reranker != zep.RerankerRrf {
			t.Fatalf("reranker = %v, want fallback default rrf", f.api.lastQuery.Reranker)
		}
	})

	t.Run("hidden param omitted from query even if model supplies it", func(t *testing.T) {
		f := newSearchToolFixture(t, WithHiddenParams(SearchParamScope))
		modelScope := "nodes"
		if _, err := f.run(context.Background(), SearchArgs{Query: "q", Scope: &modelScope}); err != nil {
			t.Fatalf("run err = %v", err)
		}
		if f.api.lastQuery.Scope != nil {
			t.Fatalf("scope = %v, want omitted (nil) because hidden", f.api.lastQuery.Scope)
		}
	})

	t.Run("mmr_lambda and center_node_uuid omitted by default (no defaults)", func(t *testing.T) {
		f := newSearchToolFixture(t)
		if _, err := f.run(context.Background(), SearchArgs{Query: "q"}); err != nil {
			t.Fatalf("run err = %v", err)
		}
		if f.api.lastQuery.MmrLambda != nil {
			t.Fatalf("mmr_lambda = %v, want nil (no default, not provided)", f.api.lastQuery.MmrLambda)
		}
		if f.api.lastQuery.CenterNodeUUID != nil {
			t.Fatalf("center_node_uuid = %v, want nil", f.api.lastQuery.CenterNodeUUID)
		}
	})

	t.Run("model-provided mmr_lambda and center_node_uuid pass through", func(t *testing.T) {
		f := newSearchToolFixture(t)
		lambda := 0.7
		center := "node-99"
		if _, err := f.run(context.Background(), SearchArgs{Query: "q", MMRLambda: &lambda, CenterNodeUUID: &center}); err != nil {
			t.Fatalf("run err = %v", err)
		}
		if f.api.lastQuery.MmrLambda == nil || *f.api.lastQuery.MmrLambda != 0.7 {
			t.Fatalf("mmr_lambda = %v, want 0.7", f.api.lastQuery.MmrLambda)
		}
		if f.api.lastQuery.CenterNodeUUID == nil || *f.api.lastQuery.CenterNodeUUID != "node-99" {
			t.Fatalf("center_node_uuid = %v, want node-99", f.api.lastQuery.CenterNodeUUID)
		}
	})

	t.Run("pinned nil hides an optional param from the SDK call", func(t *testing.T) {
		f := newSearchToolFixture(t, WithHiddenParams(SearchParamMMRLambda, SearchParamCenterNodeUUID))
		lambda := 0.9
		center := "ignored"
		if _, err := f.run(context.Background(), SearchArgs{Query: "q", MMRLambda: &lambda, CenterNodeUUID: &center}); err != nil {
			t.Fatalf("run err = %v", err)
		}
		if f.api.lastQuery.MmrLambda != nil {
			t.Fatalf("mmr_lambda = %v, want nil (hidden)", f.api.lastQuery.MmrLambda)
		}
		if f.api.lastQuery.CenterNodeUUID != nil {
			t.Fatalf("center_node_uuid = %v, want nil (hidden)", f.api.lastQuery.CenterNodeUUID)
		}
	})
}

// --- Filters / BFS pass-through ---------------------------------------------

func TestGraphSearchHandlerFiltersAndBFSAlwaysApplied(t *testing.T) {
	filters := &zep.SearchFilters{NodeLabels: []string{"Person"}}
	bfs := []string{"origin-1", "origin-2"}
	f := newSearchToolFixture(t,
		WithToolSearchFilters(filters),
		WithToolBFSOriginNodeUUIDs(bfs),
	)
	if _, err := f.run(context.Background(), SearchArgs{Query: "q"}); err != nil {
		t.Fatalf("run err = %v", err)
	}
	if f.api.lastQuery.SearchFilters != filters {
		t.Fatalf("SearchFilters = %v, want the pinned filters", f.api.lastQuery.SearchFilters)
	}
	if len(f.api.lastQuery.BfsOriginNodeUUIDs) != 2 {
		t.Fatalf("BfsOriginNodeUUIDs = %v, want %v", f.api.lastQuery.BfsOriginNodeUUIDs, bfs)
	}
}

// --- thread_summaries scope mapping -----------------------------------------

func TestGraphSearchHandlerThreadSummariesScope(t *testing.T) {
	f := newSearchToolFixture(t, WithToolSearchScope(zep.GraphSearchScopeThreadSummaries))
	f.api.searchRes = &zep.GraphSearchResults{
		ThreadSummaries: []*zep.GraphitiSagaNode{
			{Name: "thread-1", Summary: zep.String("Discussed hiking plans.")},
			{Name: "thread-2", Summary: nil},
		},
	}
	res, err := f.run(context.Background(), SearchArgs{Query: "q"})
	if err != nil {
		t.Fatalf("run err = %v", err)
	}
	if len(res.Facts) != 2 {
		t.Fatalf("Facts = %v, want 2 entries", res.Facts)
	}
	if res.Facts[0] != "thread-1: Discussed hiking plans." {
		t.Fatalf("Facts[0] = %q, want %q", res.Facts[0], "thread-1: Discussed hiking plans.")
	}
	if res.Facts[1] != "thread-2" {
		t.Fatalf("Facts[1] = %q, want %q (name-only fallback)", res.Facts[1], "thread-2")
	}
}

func TestSearchScopeSupportedIncludesThreadSummaries(t *testing.T) {
	if !searchScopeSupported(zep.GraphSearchScopeThreadSummaries) {
		t.Fatal("expected thread_summaries to be a supported scope")
	}
}

func TestMapSearchResultsThreadSummaries(t *testing.T) {
	res := &zep.GraphSearchResults{
		ThreadSummaries: []*zep.GraphitiSagaNode{
			{Name: "t1", Summary: zep.String("summary one")},
			{Name: "", Summary: zep.String("summary two")},
			{Name: "t3", Summary: nil},
			nil,
		},
	}
	got := mapSearchResults(zep.GraphSearchScopeThreadSummaries, res)
	want := []string{"t1: summary one", "summary two", "t3"}
	if len(got) != len(want) {
		t.Fatalf("mapSearchResults = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("mapSearchResults[%d] = %q, want %q", i, got[i], want[i])
		}
	}
}

// --- Error handling ----------------------------------------------------------

func TestGraphSearchHandlerZepErrorReturnsEmptyResult(t *testing.T) {
	f := newSearchToolFixture(t)
	f.api.searchErr = errors.New("boom")
	res, err := f.run(context.Background(), SearchArgs{Query: "q"})
	if err != nil {
		t.Fatalf("run err = %v, want nil (graceful mapping)", err)
	}
	if len(res.Facts) != 0 {
		t.Fatalf("Facts = %v, want empty", res.Facts)
	}
}

func TestGraphSearchHandlerEmptyQueryReturnsEmptyResult(t *testing.T) {
	f := newSearchToolFixture(t)
	res, err := f.run(context.Background(), SearchArgs{Query: ""})
	if err != nil {
		t.Fatalf("run err = %v, want nil", err)
	}
	if len(res.Facts) != 0 {
		t.Fatalf("Facts = %v, want empty", res.Facts)
	}
	if f.api.lastQuery != nil {
		t.Fatal("expected Search not to be called for an empty query")
	}
}

// --- WithHiddenParams / GraphID / UserID target resolution -----------------

func TestGraphSearchHandlerGraphIDOverridesUserID(t *testing.T) {
	f := newSearchToolFixture(t, WithGraphID("graph-1"))
	if _, err := f.run(context.Background(), SearchArgs{Query: "q"}); err != nil {
		t.Fatalf("run err = %v", err)
	}
	if f.api.lastQuery.GraphID == nil || *f.api.lastQuery.GraphID != "graph-1" {
		t.Fatalf("GraphID = %v, want graph-1", f.api.lastQuery.GraphID)
	}
	if f.api.lastQuery.UserID != nil {
		t.Fatalf("UserID = %v, want nil when GraphID is set", f.api.lastQuery.UserID)
	}
}
