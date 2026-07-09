package zepadk

import (
	"context"
	"log/slog"

	zep "github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"

	"google.golang.org/adk/memory"
	"google.golang.org/adk/session"
	"google.golang.org/genai"
)

// memoryService implements the ADK [memory.Service] interface backed by Zep's
// user-graph search. Attach it at the runner via [runner.Config.MemoryService];
// ADK's built-in memory tooling reaches it through ToolContext.SearchMemory.
type memoryService struct {
	api    zepAPI
	scope  zep.GraphSearchScope
	limit  *int
	logger *slog.Logger
}

// MemoryOption customizes the behavior of [NewMemoryService].
type MemoryOption func(*memoryService)

// WithSearchScope sets the Zep graph search scope used by the memory service.
// Defaults to [zep.GraphSearchScopeEdges] (facts). Supported scopes are edges,
// nodes, episodes, observations, thread_summaries, and auto; each is mapped
// into memory entries (auto yields the pre-materialized Context Block). An
// unsupported scope is rejected at search time: the service logs an error and
// returns no memories rather than silently swallowing results.
func WithSearchScope(scope zep.GraphSearchScope) MemoryOption {
	return func(s *memoryService) { s.scope = scope }
}

// WithSearchLimit caps the number of results returned per search.
func WithSearchLimit(limit int) MemoryOption {
	return func(s *memoryService) {
		if limit > 0 {
			s.limit = zep.Int(limit)
		}
	}
}

// WithMemoryLogger sets the [slog.Logger] used to report Zep errors. Defaults
// to [slog.Default].
func WithMemoryLogger(logger *slog.Logger) MemoryOption {
	return func(s *memoryService) {
		if logger != nil {
			s.logger = logger
		}
	}
}

// NewMemoryService returns an ADK [memory.Service] that searches the calling
// user's Zep knowledge graph. A nil client makes every operation a safe no-op,
// so the surrounding agent runs unchanged when Zep is not configured.
//
// AddSessionToMemory is intentionally a no-op: conversation turns are ingested
// live by [NewBeforeModelCallback] via Thread.AddMessages, which routes
// messages into the user's graph automatically. There is therefore nothing to
// flush at session end.
func NewMemoryService(client *zepclient.Client, opts ...MemoryOption) memory.Service {
	svc := &memoryService{
		api:    newZepAPI(client),
		scope:  zep.GraphSearchScopeEdges,
		logger: slog.Default(),
	}
	for _, opt := range opts {
		opt(svc)
	}
	return svc
}

// AddSessionToMemory is a no-op. Turns are ingested live by the
// BeforeModelCallback; see [NewMemoryService].
func (s *memoryService) AddSessionToMemory(_ context.Context, _ session.Session) error {
	return nil
}

// SearchMemory searches the user's Zep graph for information relevant to the
// query and maps each result to a [memory.Entry] according to the configured
// scope (facts for edges, entity summaries for nodes, message content for
// episodes, derived memories for observations, or the Context Block for auto).
// On a Zep failure it logs the error and returns an empty result rather than
// propagating, so a memory lookup never breaks the agent.
func (s *memoryService) SearchMemory(ctx context.Context, req *memory.SearchRequest) (*memory.SearchResponse, error) {
	out := &memory.SearchResponse{}
	if s.api == nil || req == nil || req.UserID == "" || req.Query == "" {
		return out, nil
	}

	// Reject an unsupported scope loudly: returning empty would look like "no
	// memories" when in fact we never mapped the response shape.
	if !searchScopeSupported(s.scope) {
		s.logger.Error("zepadk: unsupported memory search scope; returning no memories",
			slog.String("user_id", req.UserID), slog.String("scope", string(s.scope)))
		return out, nil
	}

	query := &zep.GraphSearchQuery{
		Query:  req.Query,
		UserID: zep.String(req.UserID),
		Scope:  &s.scope,
		Limit:  s.limit,
	}

	res, err := s.api.Search(ctx, query)
	if err != nil {
		s.logger.Error("zepadk: memory search failed; returning no memories",
			slog.String("user_id", req.UserID), slog.Any("error", err))
		return out, nil
	}

	for _, text := range mapSearchResults(s.scope, res) {
		out.Memories = append(out.Memories, memory.Entry{
			Content: genai.NewContentFromText(text, genai.RoleModel),
		})
	}
	return out, nil
}
