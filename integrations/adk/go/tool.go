package zepadk

import (
	"encoding/json"
	"log/slog"

	"github.com/google/jsonschema-go/jsonschema"

	zep "github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"

	"google.golang.org/adk/agent"
	"google.golang.org/adk/tool"
	"google.golang.org/adk/tool/functiontool"
)

// DefaultGraphSearchToolName is the name exposed to the model for the graph
// search tool created by [NewGraphSearchTool].
const DefaultGraphSearchToolName = "search_memory"

// DefaultGraphSearchToolDescription is the description exposed to the model for
// the graph search tool created by [NewGraphSearchTool].
const DefaultGraphSearchToolDescription = "Search the user's long-term memory graph in Zep for facts relevant to a natural-language query. Use this when you need to recall details about the user that are not already in the conversation."

// SearchParam identifies a model-exposable graph search parameter, used by
// [WithHiddenParams] to hide a parameter from both the model schema and the
// Zep SDK call without pinning it to a fixed value.
type SearchParam string

// Model-exposable graph search parameters. See [WithHiddenParams].
const (
	SearchParamScope          SearchParam = "scope"
	SearchParamReranker       SearchParam = "reranker"
	SearchParamLimit          SearchParam = "limit"
	SearchParamMMRLambda      SearchParam = "mmr_lambda"
	SearchParamCenterNodeUUID SearchParam = "center_node_uuid"
)

// defaultSearchScope, defaultSearchReranker, and defaultSearchLimit are the
// defaults exposed to the model when a parameter is neither pinned nor
// hidden. They mirror the Python and TypeScript zep-adk defaults exactly.
const (
	defaultSearchScope    = zep.GraphSearchScopeEdges
	defaultSearchReranker = zep.RerankerRrf
	defaultSearchLimit    = 10
)

// searchScopeEnum and searchRerankerEnum are the model-facing enum values for
// the scope and reranker parameters, in the canonical order used across
// zep-adk's Python, Go, and TypeScript implementations.
var (
	searchScopeEnum = []any{
		string(zep.GraphSearchScopeEdges),
		string(zep.GraphSearchScopeNodes),
		string(zep.GraphSearchScopeEpisodes),
		string(zep.GraphSearchScopeObservations),
		string(zep.GraphSearchScopeThreadSummaries),
		string(zep.GraphSearchScopeAuto),
	}
	searchRerankerEnum = []any{
		string(zep.RerankerRrf),
		string(zep.RerankerMmr),
		string(zep.RerankerNodeDistance),
		string(zep.RerankerEpisodeMentions),
		string(zep.RerankerCrossEncoder),
	}
)

// SearchArgs is the typed input for the graph search tool. The model-facing
// JSON schema is built explicitly (see buildSearchInputSchema) rather than
// inferred from these struct tags, so that enums, descriptions, and defaults
// can be controlled precisely and parameters can be pinned or hidden. Every
// field besides Query is a pointer so the handler can distinguish "the model
// did not supply this" (nil) from a zero value.
type SearchArgs struct {
	// Query is the natural-language search query. Always required.
	Query string `json:"query"`
	// Scope selects what to search for (see [SearchParamScope]).
	Scope *string `json:"scope,omitempty"`
	// Reranker selects the result ordering algorithm (see [SearchParamReranker]).
	Reranker *string `json:"reranker,omitempty"`
	// Limit caps the number of results returned (see [SearchParamLimit]).
	Limit *int `json:"limit,omitempty"`
	// MMRLambda balances diversity vs. relevance for the mmr reranker (see
	// [SearchParamMMRLambda]).
	MMRLambda *float64 `json:"mmr_lambda,omitempty"`
	// CenterNodeUUID is the center node for node_distance reranking (see
	// [SearchParamCenterNodeUUID]).
	CenterNodeUUID *string `json:"center_node_uuid,omitempty"`
}

// SearchResult is the typed output of the graph search tool.
type SearchResult struct {
	// Facts are the results matching the query, most relevant first. For the
	// default edge scope these are facts; for other scopes they are the
	// corresponding entity summaries, episodes, observations, thread
	// summaries, or the Context Block (auto). The field name is kept for
	// backward compatibility.
	Facts []string `json:"facts"`
}

// pinState describes how a single pinnable/exposable parameter is configured:
// pinned to a fixed value, hidden (omitted from both schema and SDK call), or
// exposed to the model with a default.
type pinState int

const (
	stateExposed pinState = iota
	statePinned
	stateHidden
)

// graphSearchToolOptions holds the resolved configuration for the search
// tool. Each model-exposable parameter tracks a pin state plus its pinned
// value (when applicable); WithHiddenParams applied after a pin downgrades
// the state to hidden.
type graphSearchToolOptions struct {
	name        string
	description string
	graphID     string
	logger      *slog.Logger

	scopeState      pinState
	scope           zep.GraphSearchScope
	rerankerState   pinState
	reranker        zep.Reranker
	limitState      pinState
	limit           int
	mmrLambdaState  pinState
	mmrLambda       float64
	centerNodeState pinState
	centerNode      string

	searchFilters      *zep.SearchFilters
	bfsOriginNodeUUIDs []string
}

// GraphSearchToolOption customizes the behavior of [NewGraphSearchTool].
type GraphSearchToolOption func(*graphSearchToolOptions)

// WithToolName overrides the tool name exposed to the model.
func WithToolName(name string) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		if name != "" {
			o.name = name
		}
	}
}

// WithToolDescription overrides the tool description exposed to the model.
func WithToolDescription(description string) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		if description != "" {
			o.description = description
		}
	}
}

// WithGraphID scopes the search to a standalone graph instead of the calling
// user's graph. UserID and GraphID are mutually exclusive in Zep; setting a
// graph ID overrides the per-user scoping.
func WithGraphID(graphID string) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) { o.graphID = graphID }
}

// WithToolSearchScope pins and hides the Zep graph search scope, removing it
// from the model's tool schema and always using the given value. Supported
// scopes are edges, nodes, episodes, observations, thread_summaries, and
// auto; each is mapped into the tool's results (auto yields the
// pre-materialized Context Block). An unsupported scope is rejected at search
// time: the tool logs an error and returns an empty result.
//
// Behavior change: prior versions of this option configured a hidden default
// scope for every search. As of this version, an absent [WithToolSearchScope]
// exposes the scope parameter to the model (default "edges") instead of
// pinning it. Pass this option explicitly to restore the old always-pinned
// behavior.
func WithToolSearchScope(scope zep.GraphSearchScope) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		o.scopeState = statePinned
		o.scope = scope
	}
}

// WithToolSearchLimit pins and hides the maximum number of results returned
// per search, removing it from the model's tool schema. A non-positive limit
// is ignored: the option is a no-op and the parameter stays exposed to the
// model, as if the option had not been passed.
//
// Behavior change: prior versions of this option configured a hidden default
// limit for every search. As of this version, an absent [WithToolSearchLimit]
// exposes the limit parameter to the model (default 10) instead of pinning
// it. Pass this option explicitly to restore the old always-pinned behavior.
func WithToolSearchLimit(limit int) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		if limit > 0 {
			o.limitState = statePinned
			o.limit = limit
		}
	}
}

// WithToolReranker pins and hides the result reranking algorithm, removing it
// from the model's tool schema and always using the given value.
func WithToolReranker(reranker zep.Reranker) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		o.rerankerState = statePinned
		o.reranker = reranker
	}
}

// WithToolMMRLambda pins and hides the MMR diversity/relevance balance,
// removing it from the model's tool schema and always using the given value.
// Only meaningful when the reranker is "mmr".
func WithToolMMRLambda(lambda float64) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		o.mmrLambdaState = statePinned
		o.mmrLambda = lambda
	}
}

// WithToolCenterNodeUUID pins and hides the center node UUID used for
// node_distance reranking, removing it from the model's tool schema and
// always using the given value.
func WithToolCenterNodeUUID(uuid string) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		o.centerNodeState = statePinned
		o.centerNode = uuid
	}
}

// WithToolSearchFilters sets Zep search filters, always applied to every
// search. Constructor-only: never exposed to the model.
func WithToolSearchFilters(filters *zep.SearchFilters) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) { o.searchFilters = filters }
}

// WithToolBFSOriginNodeUUIDs sets node UUIDs seeding a breadth-first search,
// always applied to every search. Constructor-only: never exposed to the
// model.
func WithToolBFSOriginNodeUUIDs(uuids []string) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) { o.bfsOriginNodeUUIDs = uuids }
}

// WithHiddenParams hides the given model-exposable parameters from both the
// model's tool schema and the Zep SDK call, without pinning them to a fixed
// value (the parameter is simply omitted, as if never set). This differs
// from the pinning options (e.g. [WithToolSearchScope]), which hide a
// parameter from the schema but still send a fixed value to Zep.
//
// Options are applied in the order passed to [NewGraphSearchTool], and each
// pin/hide option simply overwrites the same underlying state field — so
// when a parameter is configured by both a pinning option (e.g.
// [WithToolSearchScope]) and WithHiddenParams, whichever option is passed
// later wins. Passing WithHiddenParams after the pinning option hides the
// parameter and omits it from the Zep call entirely; passing it before
// re-pins the parameter.
func WithHiddenParams(params ...SearchParam) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		for _, p := range params {
			switch p {
			case SearchParamScope:
				o.scopeState = stateHidden
			case SearchParamReranker:
				o.rerankerState = stateHidden
			case SearchParamLimit:
				o.limitState = stateHidden
			case SearchParamMMRLambda:
				o.mmrLambdaState = stateHidden
			case SearchParamCenterNodeUUID:
				o.centerNodeState = stateHidden
			}
		}
	}
}

// WithToolLogger sets the [slog.Logger] used to report Zep errors. Defaults to
// [slog.Default].
func WithToolLogger(logger *slog.Logger) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		if logger != nil {
			o.logger = logger
		}
	}
}

// mustRaw marshals v to a json.RawMessage default value for a schema
// property. It never errors for the concrete types passed within this file
// (string, int, float64).
func mustRaw(v any) json.RawMessage {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err) // unreachable for the concrete literal types used here
	}
	return json.RawMessage(b)
}

// buildSearchInputSchema builds the explicit JSON schema for the graph
// search tool's model-facing arguments, containing "query" (always required)
// plus every exposed (non-pinned, non-hidden) parameter, with enum values,
// descriptions, and defaults matching the Python reference implementation
// exactly.
func buildSearchInputSchema(cfg *graphSearchToolOptions) *jsonschema.Schema {
	properties := map[string]*jsonschema.Schema{
		"query": {
			Type:        "string",
			Description: "Search query text (max 400 characters).",
		},
	}

	if cfg.scopeState == stateExposed {
		properties["scope"] = &jsonschema.Schema{
			Type: "string",
			Description: "What to search for: 'edges' for facts and relationships, " +
				"'nodes' for entities and their summaries, " +
				"'episodes' for raw text data (unstructured text, messages, or JSON), " +
				"'observations' for derived memories, " +
				"'thread_summaries' for incremental thread summaries, " +
				"'auto' to let Zep decide the best mix of results.",
			Enum:    searchScopeEnum,
			Default: mustRaw(string(defaultSearchScope)),
		}
	}

	if cfg.rerankerState == stateExposed {
		properties["reranker"] = &jsonschema.Schema{
			Type: "string",
			Description: "Result ordering algorithm: 'rrf' (balanced), 'mmr' (diverse), " +
				"'cross_encoder' (highest accuracy), 'episode_mentions' " +
				"(frequently referenced), 'node_distance' (near a specific entity).",
			Enum:    searchRerankerEnum,
			Default: mustRaw(string(defaultSearchReranker)),
		}
	}

	if cfg.limitState == stateExposed {
		properties["limit"] = &jsonschema.Schema{
			Type:        "integer",
			Description: "Maximum number of results to return.",
			Default:     mustRaw(defaultSearchLimit),
		}
	}

	if cfg.mmrLambdaState == stateExposed {
		properties["mmr_lambda"] = &jsonschema.Schema{
			Type: "number",
			Description: "Balance between diversity (0.0) and relevance (1.0). " +
				"Only used when reranker is 'mmr'.",
		}
	}

	if cfg.centerNodeState == stateExposed {
		properties["center_node_uuid"] = &jsonschema.Schema{
			Type: "string",
			Description: "UUID of the center node for distance-based reranking. " +
				"Required when reranker is 'node_distance'.",
		}
	}

	return &jsonschema.Schema{
		Type:       "object",
		Properties: properties,
		Required:   []string{"query"},
	}
}

// resolveScope merges the pinned/model/default scope value.
//
// The invalid-value branch is defense-in-depth only: ADK validates the
// model's arguments against the input schema (including enums) before the
// handler runs, so an invalid scope is rejected there and returned to the
// model as a tool error. This fallback can only fire if the handler is
// invoked outside functiontool.
func resolveScope(cfg *graphSearchToolOptions, modelValue *string) *zep.GraphSearchScope {
	switch cfg.scopeState {
	case statePinned:
		return cfg.scope.Ptr()
	case stateHidden:
		return nil
	default:
		if modelValue != nil {
			scope, err := zep.NewGraphSearchScopeFromString(*modelValue)
			if err != nil {
				cfg.logger.Warn("zepadk: invalid scope value from model; falling back to default",
					slog.String("value", *modelValue), slog.String("default", string(defaultSearchScope)))
				return defaultSearchScope.Ptr()
			}
			return scope.Ptr()
		}
		return defaultSearchScope.Ptr()
	}
}

// resolveReranker merges the pinned/model/default reranker value. As with
// [resolveScope], the invalid-value branch is defense-in-depth only — ADK's
// schema validation rejects invalid enum values before the handler runs.
func resolveReranker(cfg *graphSearchToolOptions, modelValue *string) *zep.Reranker {
	switch cfg.rerankerState {
	case statePinned:
		return cfg.reranker.Ptr()
	case stateHidden:
		return nil
	default:
		if modelValue != nil {
			reranker, err := zep.NewRerankerFromString(*modelValue)
			if err != nil {
				cfg.logger.Warn("zepadk: invalid reranker value from model; falling back to default",
					slog.String("value", *modelValue), slog.String("default", string(defaultSearchReranker)))
				return defaultSearchReranker.Ptr()
			}
			return reranker.Ptr()
		}
		return defaultSearchReranker.Ptr()
	}
}

// resolveLimit merges the pinned/model/default limit value.
func resolveLimit(cfg *graphSearchToolOptions, modelValue *int) *int {
	switch cfg.limitState {
	case statePinned:
		return zep.Int(cfg.limit)
	case stateHidden:
		return nil
	default:
		if modelValue != nil {
			return modelValue
		}
		return zep.Int(defaultSearchLimit)
	}
}

// resolveMMRLambda merges the pinned/model value for a parameter with no
// default (nil when neither pinned nor supplied by the model).
func resolveMMRLambda(cfg *graphSearchToolOptions, modelValue *float64) *float64 {
	switch cfg.mmrLambdaState {
	case statePinned:
		return zep.Float64(cfg.mmrLambda)
	case stateHidden:
		return nil
	default:
		return modelValue
	}
}

// resolveCenterNodeUUID merges the pinned/model value for a parameter with no
// default (nil when neither pinned nor supplied by the model).
func resolveCenterNodeUUID(cfg *graphSearchToolOptions, modelValue *string) *string {
	switch cfg.centerNodeState {
	case statePinned:
		return zep.String(cfg.centerNode)
	case stateHidden:
		return nil
	default:
		return modelValue
	}
}

// resolveGraphSearchToolOptions applies opts over the default configuration.
func resolveGraphSearchToolOptions(opts ...GraphSearchToolOption) graphSearchToolOptions {
	cfg := graphSearchToolOptions{
		name:        DefaultGraphSearchToolName,
		description: DefaultGraphSearchToolDescription,
		logger:      slog.Default(),
	}
	for _, opt := range opts {
		opt(&cfg)
	}
	return cfg
}

// newGraphSearchHandlerFromConfig builds the graph search tool's execution
// function against the given zepAPI seam and resolved configuration,
// independent of functiontool wrapping. This lets tests exercise the
// merge/filters/scope logic directly with a fake API and a minimal
// ToolContext, without going through ADK's reflection-based argument
// conversion.
func newGraphSearchHandlerFromConfig(api zepAPI, cfg *graphSearchToolOptions) func(agent.ToolContext, SearchArgs) (SearchResult, error) {
	return func(tc agent.ToolContext, args SearchArgs) (SearchResult, error) {
		out := SearchResult{}
		if api == nil || args.Query == "" {
			return out, nil
		}

		scope := resolveScope(cfg, args.Scope)

		// Reject an unsupported scope loudly rather than returning an empty
		// result that looks like "nothing found".
		if scope != nil && !searchScopeSupported(*scope) {
			cfg.logger.Error("zepadk: unsupported graph search scope; returning no facts",
				slog.String("scope", string(*scope)))
			return out, nil
		}

		query := &zep.GraphSearchQuery{
			Query:          args.Query,
			Scope:          scope,
			Reranker:       resolveReranker(cfg, args.Reranker),
			Limit:          resolveLimit(cfg, args.Limit),
			MmrLambda:      resolveMMRLambda(cfg, args.MMRLambda),
			CenterNodeUUID: resolveCenterNodeUUID(cfg, args.CenterNodeUUID),
		}
		if cfg.searchFilters != nil {
			query.SearchFilters = cfg.searchFilters
		}
		if cfg.bfsOriginNodeUUIDs != nil {
			query.BfsOriginNodeUUIDs = cfg.bfsOriginNodeUUIDs
		}
		// UserID and GraphID are mutually exclusive: prefer an explicit graph
		// ID, otherwise scope to the calling user's graph.
		if cfg.graphID != "" {
			query.GraphID = zep.String(cfg.graphID)
		} else {
			query.UserID = zep.String(tc.UserID())
		}

		res, err := api.Search(tc, query)
		if err != nil {
			cfg.logger.Error("zepadk: graph search tool failed; returning no facts",
				slog.Any("error", err))
			return out, nil
		}
		effectiveScope := defaultSearchScope
		if scope != nil {
			effectiveScope = *scope
		}
		out.Facts = mapSearchResults(effectiveScope, res)
		return out, nil
	}
}

// NewGraphSearchTool returns an ADK [tool.Tool] the model can call to search
// the user's Zep knowledge graph on demand. By default the search is scoped to
// the calling user's graph (resolved from the ToolContext); pass [WithGraphID]
// to target a standalone graph instead.
//
// Every model-exposable search parameter (scope, reranker, limit, mmr_lambda,
// center_node_uuid) can be pinned to a fixed value (hidden from the model,
// always applied), hidden entirely (omitted from both the schema and the Zep
// call), or left exposed to the model with a documented default. See
// [WithToolSearchScope], [WithToolSearchLimit], [WithToolReranker],
// [WithToolMMRLambda], [WithToolCenterNodeUUID], and [WithHiddenParams].
// [WithToolSearchFilters] and [WithToolBFSOriginNodeUUIDs] are constructor-only
// and always applied when set.
//
// A nil client yields a tool that always returns an empty result, so the
// agent can be wired with the tool regardless of whether Zep is configured.
// Zep failures are logged and surfaced to the model as an empty result rather
// than an error, so a failed lookup never aborts the model turn.
func NewGraphSearchTool(client *zepclient.Client, opts ...GraphSearchToolOption) (tool.Tool, error) {
	cfg := resolveGraphSearchToolOptions(opts...)
	api := newZepAPI(client)
	handler := newGraphSearchHandlerFromConfig(api, &cfg)
	schema := buildSearchInputSchema(&cfg)

	return functiontool.New(functiontool.Config{
		Name:        cfg.name,
		Description: cfg.description,
		InputSchema: schema,
	}, handler)
}

// newGraphSearchHandler resolves opts and builds the handler in one step; a
// convenience used by tests to exercise the handler directly against a fake
// zepAPI without going through functiontool.
func newGraphSearchHandler(api zepAPI, opts ...GraphSearchToolOption) func(agent.ToolContext, SearchArgs) (SearchResult, error) {
	cfg := resolveGraphSearchToolOptions(opts...)
	return newGraphSearchHandlerFromConfig(api, &cfg)
}
