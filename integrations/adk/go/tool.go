package zepadk

import (
	"log/slog"

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

// SearchArgs is the typed input for the graph search tool. functiontool.New
// infers the JSON schema the model sees from these struct tags.
type SearchArgs struct {
	// Query is the natural-language search query.
	Query string `json:"query" jsonschema:"the natural-language query to search the user's memory graph for"`
}

// SearchResult is the typed output of the graph search tool.
type SearchResult struct {
	// Facts are the results matching the query, most relevant first. For the
	// default edge scope these are facts; for other scopes they are the
	// corresponding entity summaries, episodes, observations, or the Context
	// Block (auto). The field name is kept for backward compatibility.
	Facts []string `json:"facts"`
}

// graphSearchToolOptions holds the resolved configuration for the search tool.
type graphSearchToolOptions struct {
	name        string
	description string
	graphID     string
	scope       zep.GraphSearchScope
	limit       *int
	logger      *slog.Logger
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

// WithToolSearchScope sets the Zep graph search scope. Defaults to
// [zep.GraphSearchScopeEdges]. Supported scopes are edges, nodes, episodes,
// observations, and auto; each is mapped into the tool's results (auto yields
// the pre-materialized Context Block). An unsupported scope is rejected at
// search time: the tool logs an error and returns an empty result.
func WithToolSearchScope(scope zep.GraphSearchScope) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) { o.scope = scope }
}

// WithToolSearchLimit caps the number of results returned per search.
func WithToolSearchLimit(limit int) GraphSearchToolOption {
	return func(o *graphSearchToolOptions) {
		if limit > 0 {
			o.limit = zep.Int(limit)
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

// NewGraphSearchTool returns an ADK [tool.Tool] the model can call to search
// the user's Zep knowledge graph on demand. By default the search is scoped to
// the calling user's graph (resolved from the ToolContext); pass [WithGraphID]
// to target a standalone graph instead.
//
// A nil client yields a tool that always returns an empty result, so the
// agent can be wired with the tool regardless of whether Zep is configured.
// Zep failures are logged and surfaced to the model as an empty result rather
// than an error, so a failed lookup never aborts the model turn.
func NewGraphSearchTool(client *zepclient.Client, opts ...GraphSearchToolOption) (tool.Tool, error) {
	cfg := graphSearchToolOptions{
		name:        DefaultGraphSearchToolName,
		description: DefaultGraphSearchToolDescription,
		scope:       zep.GraphSearchScopeEdges,
		logger:      slog.Default(),
	}
	for _, opt := range opts {
		opt(&cfg)
	}
	api := newZepAPI(client)

	handler := func(tc agent.ToolContext, args SearchArgs) (SearchResult, error) {
		out := SearchResult{}
		if api == nil || args.Query == "" {
			return out, nil
		}

		// Reject an unsupported scope loudly rather than returning an empty
		// result that looks like "nothing found".
		if !searchScopeSupported(cfg.scope) {
			cfg.logger.Error("zepadk: unsupported graph search scope; returning no facts",
				slog.String("scope", string(cfg.scope)))
			return out, nil
		}

		query := &zep.GraphSearchQuery{
			Query: args.Query,
			Scope: &cfg.scope,
			Limit: cfg.limit,
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
		out.Facts = mapSearchResults(cfg.scope, res)
		return out, nil
	}

	return functiontool.New(functiontool.Config{
		Name:        cfg.name,
		Description: cfg.description,
	}, handler)
}
