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

type MemoryGraphUUIDResolver func(context.Context, *memory.SearchRequest) string

type memoryService struct {
	api       zepAPI
	graphUUID string
	resolver  MemoryGraphUUIDResolver
	search    graphSearchRequest
	logger    *slog.Logger
}

type MemoryOption func(*memoryService)

func WithMemoryGraphUUID(uuid string) MemoryOption {
	return func(s *memoryService) { s.graphUUID = uuid }
}

func WithMemoryGraphUUIDResolver(resolve MemoryGraphUUIDResolver) MemoryOption {
	return func(s *memoryService) { s.resolver = resolve }
}

func WithSearchScope(scope SearchScope) MemoryOption {
	return func(s *memoryService) { s.search.scope = scope }
}

func WithSearchLimit(limit int) MemoryOption {
	return func(s *memoryService) {
		if limit > 0 {
			s.search.pageSize = zep.Int(limit)
		}
	}
}

func WithMemoryLogger(logger *slog.Logger) MemoryOption {
	return func(s *memoryService) {
		if logger != nil {
			s.logger = logger
		}
	}
}

func NewMemoryService(client *zepclient.Client, opts ...MemoryOption) memory.Service {
	svc := &memoryService{
		api:    newZepAPI(client),
		search: graphSearchRequest{scope: SearchScopeEdges},
		logger: slog.Default(),
	}
	for _, opt := range opts {
		opt(svc)
	}
	return svc
}

func (s *memoryService) AddSessionToMemory(context.Context, session.Session) error {
	return nil
}

func (s *memoryService) SearchMemory(ctx context.Context, req *memory.SearchRequest) (*memory.SearchResponse, error) {
	out := &memory.SearchResponse{}
	if s.api == nil || req == nil || req.Query == "" {
		return out, nil
	}
	graphUUID := s.graphUUID
	if graphUUID == "" && s.resolver != nil {
		graphUUID = s.resolver(ctx, req)
	}
	if graphUUID == "" || !searchScopeSupported(s.search.scope) {
		s.logger.Error("zepadk: graph UUID or supported search scope is required")
		return out, nil
	}
	query := s.search
	query.query = req.Query
	results, err := s.api.Search(ctx, graphUUID, query)
	if err != nil {
		s.logger.Error("zepadk: memory search failed; returning no memories", slog.Any("error", err))
		return out, nil
	}
	for _, text := range results {
		out.Memories = append(out.Memories, memory.Entry{Content: genai.NewContentFromText(text, genai.RoleModel)})
	}
	return out, nil
}
