package zepadk

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"testing"

	zep "github.com/getzep/zep-go/v4"
	"google.golang.org/adk/agent"
	"google.golang.org/adk/memory"
	"google.golang.org/adk/model"
	"google.golang.org/adk/session"
	"google.golang.org/genai"
)

type fakeZepAPI struct {
	added         []*zep.AddMessage
	addCalls      int
	lastAddReq    *zep.AddMessagesRequest
	addErr        error
	context       string
	searchResults []string
	searchErr     error
	addThreadUUID string
	searchGraph   string
	searchRequest graphSearchRequest
}

func (f *fakeZepAPI) AddMessages(_ context.Context, threadUUID string, req *zep.AddMessagesRequest) (*zep.AddMessagesResult, error) {
	f.addCalls++
	f.lastAddReq = req
	f.addThreadUUID = threadUUID
	f.added = append(f.added, req.Messages...)
	if f.addErr != nil {
		return nil, f.addErr
	}
	return &zep.AddMessagesResult{Context: zep.String(f.context)}, nil
}

func (f *fakeZepAPI) Search(_ context.Context, graphUUID string, req graphSearchRequest) ([]string, error) {
	f.searchGraph = graphUUID
	f.searchRequest = req
	return f.searchResults, f.searchErr
}

func (*fakeZepAPI) CreateUser(context.Context, *zep.CreateUserRequest) (*zep.User, error) {
	return nil, errors.New("not implemented in test fake")
}

func (*fakeZepAPI) CreateThread(context.Context, *zep.CreateThreadRequest) (*zep.Thread, error) {
	return nil, errors.New("not implemented in test fake")
}

type fakeCallbackContext struct {
	context.Context
	content *genai.Content
}

func newFakeCallbackContext(content *genai.Content) *fakeCallbackContext {
	return &fakeCallbackContext{Context: context.Background(), content: content}
}

func (c *fakeCallbackContext) UserContent() *genai.Content        { return c.content }
func (*fakeCallbackContext) InvocationID() string                 { return "invocation" }
func (*fakeCallbackContext) AgentName() string                    { return "agent" }
func (*fakeCallbackContext) ReadonlyState() session.ReadonlyState { return nil }
func (*fakeCallbackContext) UserID() string                       { return "adk-user" }
func (*fakeCallbackContext) AppName() string                      { return "app" }
func (*fakeCallbackContext) SessionID() string                    { return "adk-session" }
func (*fakeCallbackContext) Branch() string                       { return "" }
func (*fakeCallbackContext) Artifacts() agent.Artifacts           { return nil }
func (*fakeCallbackContext) State() session.State                 { return nil }

var _ agent.CallbackContext = (*fakeCallbackContext)(nil)

func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func TestBeforeCallbackPersistsWithConfiguredUUIDs(t *testing.T) {
	api := &fakeZepAPI{context: "remembered fact"}
	cb := newBeforeModelCallback(nil, api,
		WithUserUUID("user-uuid"),
		WithThreadUUID("thread-uuid"),
		WithLogger(discardLogger()))
	cc := &fakeCallbackContext{Context: context.Background()}
	cc.content = genai.NewContentFromText("hello", genai.RoleUser)
	req := &model.LLMRequest{Contents: []*genai.Content{cc.content}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("callback error = %v", err)
	}
	if api.addThreadUUID != "thread-uuid" || len(api.added) != 1 {
		t.Fatalf("persisted thread=%q messages=%d", api.addThreadUUID, len(api.added))
	}
	if api.added[0].GetRole() == nil || *api.added[0].GetRole() != zep.RoleTypeUser {
		t.Fatalf("persisted role = %v, want user", api.added[0].GetRole())
	}
	if got := LastUserText(req.Config.SystemInstruction); got == "" {
		t.Fatal("callback did not inject the returned context")
	}
}

func TestBeforeCallbackDoesNotPersistToolLoopContinuation(t *testing.T) {
	api := &fakeZepAPI{}
	cb := newBeforeModelCallback(nil, api,
		WithUserUUID("user-uuid"), WithThreadUUID("thread-uuid"),
		WithLogger(discardLogger()))
	cc := &fakeCallbackContext{Context: context.Background()}
	cc.content = genai.NewContentFromText("hello", genai.RoleUser)
	req := &model.LLMRequest{Contents: []*genai.Content{
		cc.content,
		{Parts: []*genai.Part{genai.NewPartFromFunctionResponse("lookup", map[string]any{"ok": true})}},
	}}
	if _, err := cb(cc, req); err != nil {
		t.Fatalf("callback error = %v", err)
	}
	if len(api.added) != 0 {
		t.Fatalf("persisted %d messages during tool loop", len(api.added))
	}
}

func TestBeforeCallbackContextBuilderReceivesResolvedUUIDs(t *testing.T) {
	api := &fakeZepAPI{}
	var input ContextInput
	cb := newBeforeModelCallback(nil, api,
		WithUserUUID("user-uuid"), WithThreadUUID("thread-uuid"),
		WithContextBuilder(func(_ context.Context, in ContextInput) (string, error) {
			input = in
			return "builder context", nil
		}),
		WithLogger(discardLogger()))
	cc := &fakeCallbackContext{Context: context.Background()}
	cc.content = genai.NewContentFromText("hello", genai.RoleUser)
	req := &model.LLMRequest{Contents: []*genai.Content{cc.content}}
	if _, err := cb(cc, req); err != nil {
		t.Fatalf("callback error = %v", err)
	}
	if input.UserUUID != "user-uuid" || input.ThreadUUID != "thread-uuid" {
		t.Fatalf("builder UUIDs = (%q, %q)", input.UserUUID, input.ThreadUUID)
	}
	if got := LastUserText(req.Config.SystemInstruction); got == "" {
		t.Fatal("builder context was not injected")
	}
}

func TestAfterCallbackPersistsAssistantMessage(t *testing.T) {
	api := &fakeZepAPI{}
	cb := newAfterModelCallback(api, WithAfterThreadUUID("thread-uuid"), WithAfterLogger(discardLogger()))
	cc := &fakeCallbackContext{Context: context.Background()}
	resp := &model.LLMResponse{Content: genai.NewContentFromText("answer", genai.RoleModel)}
	if _, err := cb(cc, resp, nil); err != nil {
		t.Fatalf("callback error = %v", err)
	}
	if api.addThreadUUID != "thread-uuid" || len(api.added) != 1 {
		t.Fatalf("persisted thread=%q messages=%d", api.addThreadUUID, len(api.added))
	}
	if api.added[0].GetRole() == nil || *api.added[0].GetRole() != zep.RoleTypeAssistant {
		t.Fatalf("persisted role = %v, want assistant", api.added[0].GetRole())
	}
}

func TestMemoryServiceUsesConfiguredGraphUUID(t *testing.T) {
	api := &fakeZepAPI{searchResults: []string{"fact one", "fact two"}}
	svc := &memoryService{
		api: api, graphUUID: "graph-uuid",
		search: graphSearchRequest{scope: SearchScopeNodes},
		logger: discardLogger(),
	}
	resp, err := svc.SearchMemory(context.Background(), &memory.SearchRequest{Query: "query"})
	if err != nil {
		t.Fatalf("SearchMemory error = %v", err)
	}
	if api.searchGraph != "graph-uuid" || len(resp.Memories) != 2 {
		t.Fatalf("graph=%q memories=%d", api.searchGraph, len(resp.Memories))
	}
	if api.searchRequest.scope != SearchScopeNodes {
		t.Fatalf("search scope = %q, want nodes", api.searchRequest.scope)
	}
}

func TestMemoryServiceReturnsEmptyResultOnSearchError(t *testing.T) {
	svc := &memoryService{
		api:       &fakeZepAPI{searchErr: errors.New("offline")},
		graphUUID: "graph-uuid",
		search:    graphSearchRequest{scope: SearchScopeEdges},
		logger:    discardLogger(),
	}
	resp, err := svc.SearchMemory(context.Background(), &memory.SearchRequest{Query: "query"})
	if err != nil || len(resp.Memories) != 0 {
		t.Fatalf("SearchMemory = (%v, %v), want empty response without error", resp, err)
	}
}

func TestSearchScopeAndRerankerValidation(t *testing.T) {
	for _, scope := range []SearchScope{
		SearchScopeEdges, SearchScopeNodes, SearchScopeEpisodes,
		SearchScopeObservations, SearchScopeThreadSummaries, SearchScopeAuto,
	} {
		if !searchScopeSupported(scope) {
			t.Errorf("scope %q is not supported", scope)
		}
	}
	for _, reranker := range []SearchReranker{
		SearchRerankerRRF, SearchRerankerMMR, SearchRerankerNodeDistance,
		SearchRerankerEpisodeMentions, SearchRerankerCrossEncoder,
	} {
		if !searchRerankerSupported(reranker) {
			t.Errorf("reranker %q is not supported", reranker)
		}
	}
	if searchScopeSupported(SearchScope("invalid")) || searchRerankerSupported(SearchReranker("invalid")) {
		t.Fatal("invalid scope or reranker was accepted")
	}
}

func TestToolTypedSearchFilterOption(t *testing.T) {
	filters := &zep.EdgeSearchFilters{}
	cfg := &graphSearchToolOptions{}
	WithToolEdgeSearchFilters(filters)(cfg)
	if cfg.scopeFilters[SearchScopeEdges] != filters {
		t.Fatal("edge filter option did not preserve the typed filter")
	}
}

func TestCreateHelpersRejectNilClient(t *testing.T) {
	if _, _, err := CreateUser(context.Background(), nil, "Jane", "Smith", "jane@example.com"); err == nil {
		t.Fatal("CreateUser(nil) returned no error")
	}
	if _, err := CreateThread(context.Background(), nil, "user-uuid"); err == nil {
		t.Fatal("CreateThread(nil) returned no error")
	}
}
