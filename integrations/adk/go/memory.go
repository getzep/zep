package zepadk

import (
	"context"
	"log/slog"

	zep "github.com/getzep/zep-go/v4"
	zepclient "github.com/getzep/zep-go/v4/client"

	"google.golang.org/adk/memory"
	"google.golang.org/adk/session"
	"google.golang.org/genai"
)

// GraphUUIDResolver returns the UUID of the Zep graph to search for the ADK
// memory request req. An application that keeps one graph for each user reads
// the UUID from its own database with req.UserID as the key. The resolver
// returns "" when the application has no graph UUID for the request, and the
// memory service then returns no memories.
type GraphUUIDResolver func(req *memory.SearchRequest) string

// memoryService implements the ADK [memory.Service] interface backed by Zep
// graph search. Attach it at the runner via [runner.Config.MemoryService];
// ADK's built-in memory tooling reaches it through ToolContext.SearchMemory.
type memoryService struct {
	api       zepAPI
	scope     SearchScope
	limit     *int
	graphUUID GraphUUIDResolver
	logger    *slog.Logger
}

// MemoryOption customizes the behavior of [NewMemoryService].
type MemoryOption func(*memoryService)

// WithSearchScope sets the Zep graph search scope used by the memory service.
// Defaults to [SearchScopeEdges] (facts). Supported scopes are edges, nodes,
// episodes, observations, thread_summaries, and auto; each is mapped into
// memory entries (auto yields the assembled context block). An unsupported
// scope is rejected at search time: the service logs an error and returns no
// memories rather than silently swallowing results.
func WithSearchScope(scope SearchScope) MemoryOption {
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

// WithMemoryGraphUUID sets the UUID of the Zep graph that every search reads.
// Use it when one agent instance serves one user or one standalone graph. An
// application that serves many users passes [WithMemoryGraphUUIDResolver].
//
// The UUID of the graph of a user is the GraphUUID field of the user, which
// [CreateUser] returns.
func WithMemoryGraphUUID(graphUUID string) MemoryOption {
	return func(s *memoryService) {
		s.graphUUID = func(*memory.SearchRequest) string { return graphUUID }
	}
}

// WithMemoryGraphUUIDResolver sets the resolver that maps each ADK memory
// request to a Zep graph UUID. The resolver reads the UUID from the
// application's own store. It must not call Zep, because a lookup on every
// search adds a round-trip to the request path.
func WithMemoryGraphUUIDResolver(resolver GraphUUIDResolver) MemoryOption {
	return func(s *memoryService) {
		if resolver != nil {
			s.graphUUID = resolver
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

// NewMemoryService returns an ADK [memory.Service] that searches a Zep
// knowledge graph. A nil client makes every operation a safe no-op, so the
// surrounding agent runs unchanged when Zep is not configured.
//
// Zep v4 addresses a graph by a server-generated UUID, and the ADK user ID is
// not such a UUID. The application therefore supplies the graph UUID through
// [WithMemoryGraphUUID] or [WithMemoryGraphUUIDResolver]. Without one the
// service returns no memories.
//
// AddSessionToMemory is intentionally a no-op: conversation turns are ingested
// live by [NewBeforeModelCallback] via Thread.AddMessages, which routes
// messages into the user's graph automatically. There is therefore nothing to
// flush at session end.
func NewMemoryService(client *zepclient.Client, opts ...MemoryOption) memory.Service {
	svc := &memoryService{
		api:    newZepAPI(client),
		scope:  SearchScopeEdges,
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

// SearchMemory searches the configured Zep graph for information relevant to
// the query and maps each result to a [memory.Entry] according to the
// configured scope (facts for edges, entity summaries for nodes, message
// content for episodes, derived memories for observations, or the context
// block for auto). On a Zep failure it logs the error and returns an empty
// result rather than propagating, so a memory lookup never breaks the agent.
func (s *memoryService) SearchMemory(ctx context.Context, req *memory.SearchRequest) (*memory.SearchResponse, error) {
	out := &memory.SearchResponse{}
	if s.api == nil || req == nil || req.Query == "" {
		return out, nil
	}

	graphUUID := ""
	if s.graphUUID != nil {
		graphUUID = s.graphUUID(req)
	}
	if graphUUID == "" {
		s.logger.Error("zepadk: no Zep graph UUID for this memory search; returning no memories",
			slog.String("user_id", req.UserID))
		return out, nil
	}

	// Reject an unsupported scope loudly: returning empty would look like "no
	// memories" when in fact we never mapped the response shape.
	if !searchScopeSupported(s.scope) {
		s.logger.Error("zepadk: unsupported memory search scope; returning no memories",
			slog.String("user_id", req.UserID), slog.String("scope", string(s.scope)))
		return out, nil
	}

	texts, err := searchGraph(ctx, s.api, s.scope, graphUUID, s.limit, &zep.SearchRequest{Query: req.Query})
	if err != nil {
		s.logger.Error("zepadk: memory search failed; returning no memories",
			slog.String("user_id", req.UserID), slog.Any("error", err))
		return out, nil
	}

	for _, text := range texts {
		out.Memories = append(out.Memories, memory.Entry{
			Content: genai.NewContentFromText(text, genai.RoleModel),
		})
	}
	return out, nil
}
