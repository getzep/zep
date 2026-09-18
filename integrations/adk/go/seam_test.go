package zepadk

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"strconv"
	"strings"
	"testing"
	"unicode/utf8"

	zep "github.com/getzep/zep-go/v4"

	"google.golang.org/adk/agent"
	"google.golang.org/adk/memory"
	"google.golang.org/adk/model"
	"google.golang.org/adk/session"
	"google.golang.org/genai"
)

// discardLogger returns a logger that drops all output, keeping test logs quiet
// while still exercising the warning/error code paths.
func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func validUTF8(s string) bool { return utf8.ValidString(s) }

// fakeZepAPI is an in-memory implementation of the zepAPI seam used to
// table-test the success paths (persist / inject / dedup / scope-mapping)
// without a live Zep account or HTTP mocking.
type fakeZepAPI struct {
	added          []*zep.AddMessage
	contextOut     string // returned as resp.Context from AddMessages
	addErr         error
	addMsgCalls    int
	lastAddMsgReq  *zep.AddMessagesRequest
	lastThreadUUID string

	edges           []*zep.Edge
	nodes           []*zep.Node
	episodes        []*zep.Episode
	observations    []*zep.Observation
	threadSummaries []*zep.ThreadSummary
	graphContext    string
	searchErr       error
	searchCalls     int
	lastSearchScope SearchScope
	lastGraphUUID   string
	lastSearchBody  *zep.SearchRequest
	lastSearchLimit *int

	createUserErr       error
	createUserCalls     int
	lastCreateUserReq   *zep.CreateUserRequest
	createThreadErr     error
	createThreadCalls   int
	lastCreateThreadReq *zep.CreateThreadRequest
}

func (f *fakeZepAPI) AddMessages(_ context.Context, threadUUID string, req *zep.AddMessagesRequest) (*zep.AddMessagesResult, error) {
	f.addMsgCalls++
	f.lastAddMsgReq = req
	f.lastThreadUUID = threadUUID
	if f.addErr != nil {
		return nil, f.addErr
	}
	f.added = append(f.added, req.Messages...)
	res := &zep.AddMessagesResult{}
	if f.contextOut != "" {
		res.Context = zep.String(f.contextOut)
	}
	return res, nil
}

// recordSearch stores the arguments of a search call and reports whether the
// fake must return an error.
func (f *fakeZepAPI) recordSearch(scope SearchScope, graphUUID string, limit *int, body *zep.SearchRequest) error {
	f.searchCalls++
	f.lastSearchScope = scope
	f.lastGraphUUID = graphUUID
	f.lastSearchLimit = limit
	f.lastSearchBody = body
	return f.searchErr
}

func (f *fakeZepAPI) SearchEdges(_ context.Context, graphUUID string, req *zep.GraphSearchEdgesRequest) ([]*zep.Edge, error) {
	if err := f.recordSearch(SearchScopeEdges, graphUUID, req.Limit, req.Body); err != nil {
		return nil, err
	}
	return f.edges, nil
}

func (f *fakeZepAPI) SearchNodes(_ context.Context, graphUUID string, req *zep.GraphSearchNodesRequest) ([]*zep.Node, error) {
	if err := f.recordSearch(SearchScopeNodes, graphUUID, req.Limit, req.Body); err != nil {
		return nil, err
	}
	return f.nodes, nil
}

func (f *fakeZepAPI) SearchEpisodes(_ context.Context, graphUUID string, req *zep.GraphSearchEpisodesRequest) ([]*zep.Episode, error) {
	if err := f.recordSearch(SearchScopeEpisodes, graphUUID, req.Limit, req.Body); err != nil {
		return nil, err
	}
	return f.episodes, nil
}

func (f *fakeZepAPI) SearchObservations(_ context.Context, graphUUID string, req *zep.GraphSearchObservationsRequest) ([]*zep.Observation, error) {
	if err := f.recordSearch(SearchScopeObservations, graphUUID, req.Limit, req.Body); err != nil {
		return nil, err
	}
	return f.observations, nil
}

func (f *fakeZepAPI) SearchThreadSummaries(_ context.Context, graphUUID string, req *zep.GraphSearchThreadSummariesRequest) ([]*zep.ThreadSummary, error) {
	if err := f.recordSearch(SearchScopeThreadSummaries, graphUUID, req.Limit, req.Body); err != nil {
		return nil, err
	}
	return f.threadSummaries, nil
}

func (f *fakeZepAPI) GetGraphContext(_ context.Context, graphUUID string, req *zep.GraphContextRequest) (*zep.GraphContextResponse, error) {
	if err := f.recordSearch(SearchScopeAuto, graphUUID, nil, &zep.SearchRequest{Query: req.Query, Filters: req.Filters}); err != nil {
		return nil, err
	}
	return &zep.GraphContextResponse{Context: zep.String(f.graphContext)}, nil
}

func (f *fakeZepAPI) CreateUser(_ context.Context, req *zep.CreateUserRequest) (*zep.User, error) {
	f.createUserCalls++
	f.lastCreateUserReq = req
	if f.createUserErr != nil {
		return nil, f.createUserErr
	}
	return &zep.User{UUID: zep.String("user-uuid-1"), GraphUUID: zep.String("graph-uuid-1")}, nil
}

func (f *fakeZepAPI) CreateThread(_ context.Context, req *zep.CreateThreadRequest) (*zep.Thread, error) {
	f.createThreadCalls++
	f.lastCreateThreadReq = req
	if f.createThreadErr != nil {
		return nil, f.createThreadErr
	}
	return &zep.Thread{UUID: zep.String("thread-uuid-1"), UserUUID: zep.String(req.UserUUID)}, nil
}

// --- minimal CallbackContext stub ---------------------------------------

// fakeCallbackContext is a trivial agent.CallbackContext that returns a fixed
// session ID and user content, enough to drive the before/after callbacks.
type fakeCallbackContext struct {
	context.Context
	sessionID string
	userID    string
	content   *genai.Content
}

func newFakeCallbackContext(sessionID, userID string, content *genai.Content) *fakeCallbackContext {
	return &fakeCallbackContext{Context: context.Background(), sessionID: sessionID, userID: userID, content: content}
}

func (c *fakeCallbackContext) UserContent() *genai.Content          { return c.content }
func (c *fakeCallbackContext) InvocationID() string                 { return "inv" }
func (c *fakeCallbackContext) AgentName() string                    { return "agent" }
func (c *fakeCallbackContext) ReadonlyState() session.ReadonlyState { return nil }
func (c *fakeCallbackContext) UserID() string                       { return c.userID }
func (c *fakeCallbackContext) AppName() string                      { return "app" }
func (c *fakeCallbackContext) SessionID() string                    { return c.sessionID }
func (c *fakeCallbackContext) Branch() string                       { return "" }
func (c *fakeCallbackContext) Artifacts() agent.Artifacts           { return nil }
func (c *fakeCallbackContext) State() session.State                 { return nil }

var _ agent.CallbackContext = (*fakeCallbackContext)(nil)

// --- BeforeModelCallback: new-turn persist + inject ----------------------

func TestBeforeModelCallbackPersistsAndInjects(t *testing.T) {
	api := &fakeZepAPI{contextOut: "USER FACTS"}
	cb := newBeforeModelCallback(nil, api, WithThreadUUID("thread-uuid-1"), WithUserMessageName("Jane"))

	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi there", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi there", genai.RoleUser)}}

	resp, err := cb(cc, req)
	if err != nil || resp != nil {
		t.Fatalf("cb = (%v, %v), want (nil, nil)", resp, err)
	}
	if api.addMsgCalls != 1 || len(api.added) != 1 {
		t.Fatalf("want exactly one persisted message, got calls=%d added=%d", api.addMsgCalls, len(api.added))
	}
	msg := api.added[0]
	if msg.Role == nil || *msg.Role != zep.RoleTypeUser || deref(msg.Content) != "hi there" {
		t.Fatalf("persisted message = %+v, want user role with content", msg)
	}
	if msg.Name == nil || *msg.Name != "Jane" {
		t.Fatalf("message name = %v, want Jane", msg.Name)
	}
	// Context block injected through the default template wrapper.
	got := LastUserText(req.Config.SystemInstruction)
	if !strings.Contains(got, "USER FACTS") || !strings.Contains(got, "<ZEP_CONTEXT>") {
		t.Fatalf("system instruction = %q, want default template + context block", got)
	}
}

// --- BeforeModelCallback: tool-loop dedup (the critical fix) -------------

func TestBeforeModelCallbackSkipsToolLoopContinuation(t *testing.T) {
	api := &fakeZepAPI{contextOut: "USER FACTS"}
	cb := newBeforeModelCallback(nil, api, WithThreadUUID("thread-uuid-1"))

	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("what do you know about me?", genai.RoleUser))

	// First pass: genuine user turn -> persist + inject.
	req := &model.LLMRequest{Contents: []*genai.Content{
		genai.NewContentFromText("what do you know about me?", genai.RoleUser),
	}}
	if _, err := cb(cc, req); err != nil {
		t.Fatalf("first pass err: %v", err)
	}
	if api.addMsgCalls != 1 {
		t.Fatalf("first pass: AddMessages calls = %d, want 1", api.addMsgCalls)
	}

	// Tool-loop continuation: latest content is the search_memory function
	// response. Must NOT persist again, must NOT inject again.
	cont := &model.LLMRequest{Contents: []*genai.Content{
		genai.NewContentFromText("what do you know about me?", genai.RoleUser),
		genai.NewContentFromFunctionCall("search_memory", map[string]any{"query": "me"}, genai.RoleModel),
		genai.NewContentFromFunctionResponse("search_memory", map[string]any{"facts": []string{"x"}}, genai.RoleUser),
	}}
	if _, err := cb(cc, cont); err != nil {
		t.Fatalf("continuation err: %v", err)
	}
	if api.addMsgCalls != 1 {
		t.Fatalf("continuation re-persisted the user message: AddMessages calls = %d, want 1", api.addMsgCalls)
	}
	if cont.Config != nil {
		t.Fatalf("continuation re-injected context block: Config = %+v, want nil", cont.Config)
	}
}

func TestIsToolLoopContinuation(t *testing.T) {
	userTurn := genai.NewContentFromText("hello", genai.RoleUser)
	funcCall := genai.NewContentFromFunctionCall("search_memory", map[string]any{"q": "x"}, genai.RoleModel)
	funcResp := genai.NewContentFromFunctionResponse("search_memory", map[string]any{"facts": []string{}}, genai.RoleUser)

	tests := []struct {
		name     string
		contents []*genai.Content
		want     bool
	}{
		{name: "nil", contents: nil, want: false},
		{name: "empty", contents: []*genai.Content{}, want: false},
		{name: "single user turn", contents: []*genai.Content{userTurn}, want: false},
		{name: "latest is function response", contents: []*genai.Content{userTurn, funcCall, funcResp}, want: true},
		{name: "latest is plain text (model reply)", contents: []*genai.Content{userTurn, funcCall, funcResp, userTurn}, want: false},
		{name: "trailing nil content ignored", contents: []*genai.Content{userTurn, funcResp, nil}, want: true},
		{name: "trailing empty-parts content ignored", contents: []*genai.Content{userTurn, funcResp, {}}, want: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := IsToolLoopContinuation(tc.contents); got != tc.want {
				t.Fatalf("IsToolLoopContinuation() = %v, want %v", got, tc.want)
			}
		})
	}
}

// --- BeforeModelCallback: message truncation ----------------------------

func TestBeforeModelCallbackTruncatesOversizeMessage(t *testing.T) {
	api := &fakeZepAPI{}
	cb := newBeforeModelCallback(nil, api, WithThreadUUID("thread-uuid-1"))

	huge := strings.Repeat("a", maxMessageContentChars+500)
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText(huge, genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText(huge, genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v", err)
	}
	if len(api.added) != 1 {
		t.Fatalf("want one persisted message, got %d", len(api.added))
	}
	got := deref(api.added[0].Content)
	if len(got) > maxMessageContentChars {
		t.Fatalf("persisted content len = %d, exceeds limit %d", len(got), maxMessageContentChars)
	}
	if len(got) != messageTruncateChars {
		t.Fatalf("persisted content len = %d, want truncate target %d", len(got), messageTruncateChars)
	}
	// Content must be truncated, never dropped.
	if got == "" {
		t.Fatal("oversize message was dropped to empty; must truncate instead")
	}
}

// --- AfterModelCallback: persists assistant reply -----------------------

func TestAfterModelCallbackPersistsAssistantReply(t *testing.T) {
	api := &fakeZepAPI{}
	cb := newAfterModelCallback(api, WithAfterThreadUUID("thread-uuid-1"), WithAssistantMessageName("assistant"))

	cc := newFakeCallbackContext("thread-1", "u1", nil)
	resp := &model.LLMResponse{Content: genai.NewContentFromText("here is your answer", genai.RoleModel)}

	out, err := cb(cc, resp, nil)
	if err != nil || out != nil {
		t.Fatalf("cb = (%v, %v), want (nil, nil)", out, err)
	}
	if len(api.added) != 1 {
		t.Fatalf("want one persisted assistant message, got %d", len(api.added))
	}
	msg := api.added[0]
	if msg.Role == nil || *msg.Role != zep.RoleTypeAssistant {
		t.Fatalf("role = %v, want assistant", msg.Role)
	}
	if deref(msg.Content) != "here is your answer" {
		t.Fatalf("content = %q, want assistant reply", deref(msg.Content))
	}
	if msg.Name == nil || *msg.Name != "assistant" {
		t.Fatalf("name = %v, want assistant", msg.Name)
	}
}

func TestAfterModelCallbackSkips(t *testing.T) {
	funcCallOnly := &genai.Content{Parts: []*genai.Part{
		genai.NewPartFromFunctionCall("search_memory", map[string]any{"q": "x"}),
	}}

	tests := []struct {
		name string
		resp *model.LLMResponse
		err  error
	}{
		{name: "model error", resp: &model.LLMResponse{Content: genai.NewContentFromText("x", genai.RoleModel)}, err: errors.New("boom")},
		{name: "nil response", resp: nil},
		{name: "partial chunk", resp: &model.LLMResponse{Partial: true, Content: genai.NewContentFromText("frag", genai.RoleModel)}},
		{name: "function-call only (tool step)", resp: &model.LLMResponse{Content: funcCallOnly}},
		{name: "empty content", resp: &model.LLMResponse{Content: &genai.Content{}}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			api := &fakeZepAPI{}
			cb := newAfterModelCallback(api, WithAfterThreadUUID("thread-uuid-1"))
			cc := newFakeCallbackContext("thread-1", "u1", nil)
			if _, err := cb(cc, tc.resp, tc.err); err != nil {
				t.Fatalf("cb err: %v", err)
			}
			if api.addMsgCalls != 0 {
				t.Fatalf("expected no persistence, got %d AddMessages calls", api.addMsgCalls)
			}
		})
	}
}

func TestAfterModelCallbackTruncatesOversizeReply(t *testing.T) {
	api := &fakeZepAPI{}
	cb := newAfterModelCallback(api, WithAfterThreadUUID("thread-uuid-1"))

	huge := strings.Repeat("b", maxMessageContentChars+200)
	cc := newFakeCallbackContext("thread-1", "u1", nil)
	resp := &model.LLMResponse{Content: genai.NewContentFromText(huge, genai.RoleModel)}

	if _, err := cb(cc, resp, nil); err != nil {
		t.Fatalf("cb err: %v", err)
	}
	if len(api.added) != 1 {
		t.Fatalf("want one persisted message, got %d", len(api.added))
	}
	if got := len(deref(api.added[0].Content)); got > maxMessageContentChars || got != messageTruncateChars {
		t.Fatalf("persisted reply len = %d, want %d (<= %d)", got, messageTruncateChars, maxMessageContentChars)
	}
}

// --- AssistantText -------------------------------------------------------

func TestAssistantText(t *testing.T) {
	tests := []struct {
		name    string
		content *genai.Content
		want    string
	}{
		{name: "nil", content: nil, want: ""},
		{name: "single text", content: genai.NewContentFromText("hello", genai.RoleModel), want: "hello"},
		{
			name: "joins multiple text parts",
			content: &genai.Content{Parts: []*genai.Part{
				genai.NewPartFromText("part one"),
				genai.NewPartFromText("part two"),
			}},
			want: "part one part two",
		},
		{
			name: "function call only -> empty",
			content: &genai.Content{Parts: []*genai.Part{
				genai.NewPartFromFunctionCall("search_memory", map[string]any{"q": "x"}),
			}},
			want: "",
		},
		{
			name: "skips nil and function-call parts",
			content: &genai.Content{Parts: []*genai.Part{
				genai.NewPartFromFunctionCall("search_memory", map[string]any{"q": "x"}),
				genai.NewPartFromText("real reply"),
				nil,
			}},
			want: "real reply",
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := AssistantText(tc.content); got != tc.want {
				t.Fatalf("AssistantText() = %q, want %q", got, tc.want)
			}
		})
	}
}

// --- MemoryService scope mapping (the high-severity fix) ----------------

func TestMemoryServiceScopeMapping(t *testing.T) {
	tests := []struct {
		name  string
		scope SearchScope
		api   *fakeZepAPI
		want  []string
	}{
		{
			name:  "edges -> facts",
			scope: SearchScopeEdges,
			api:   &fakeZepAPI{edges: []*zep.Edge{{Fact: zep.String("f1")}, {Fact: zep.String("")}, nil, {Fact: zep.String("f2")}}},
			want:  []string{"f1", "f2"},
		},
		{
			name:  "nodes -> name and summary",
			scope: SearchScopeNodes,
			api: &fakeZepAPI{nodes: []*zep.Node{
				{Name: zep.String("Jane"), Summary: zep.String("vegetarian")},
				{Name: zep.String("Bob")},
				nil,
			}},
			want: []string{"Jane: vegetarian", "Bob"},
		},
		{
			name:  "episodes -> content",
			scope: SearchScopeEpisodes,
			api:   &fakeZepAPI{episodes: []*zep.Episode{{Content: zep.String("ep1")}, {Content: zep.String("")}, {Content: zep.String("ep2")}}},
			want:  []string{"ep1", "ep2"},
		},
		{
			name:  "observations -> name and summary",
			scope: SearchScopeObservations,
			api: &fakeZepAPI{observations: []*zep.Observation{
				{Name: zep.String("diet"), Summary: zep.String("Jane is a vegetarian.")},
				nil,
			}},
			want: []string{"diet: Jane is a vegetarian."},
		},
		{
			name:  "thread_summaries -> summary",
			scope: SearchScopeThreadSummaries,
			api: &fakeZepAPI{threadSummaries: []*zep.ThreadSummary{
				{Summary: zep.String("Discussed hiking plans.")},
				{},
				nil,
			}},
			want: []string{"Discussed hiking plans."},
		},
		{
			name:  "auto -> context block",
			scope: SearchScopeAuto,
			api:   &fakeZepAPI{graphContext: "THE CONTEXT BLOCK"},
			want:  []string{"THE CONTEXT BLOCK"},
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			svc := &memoryService{
				api:       tc.api,
				scope:     tc.scope,
				graphUUID: func(*memory.SearchRequest) string { return "graph-uuid-1" },
				logger:    discardLogger(),
			}
			resp, err := svc.SearchMemory(context.Background(), &memory.SearchRequest{UserID: "u1", Query: "q"})
			if err != nil {
				t.Fatalf("SearchMemory err: %v", err)
			}
			got := entryTexts(resp)
			if !equalStrings(got, tc.want) {
				t.Fatalf("memories = %v, want %v", got, tc.want)
			}
			// The configured scope must select the matching search method,
			// and the graph UUID must reach it.
			if tc.api.lastSearchScope != tc.scope {
				t.Fatalf("search scope = %q, want %q", tc.api.lastSearchScope, tc.scope)
			}
			if tc.api.lastGraphUUID != "graph-uuid-1" {
				t.Fatalf("graph UUID = %q, want graph-uuid-1", tc.api.lastGraphUUID)
			}
		})
	}
}

func TestMemoryServiceRejectsUnsupportedScope(t *testing.T) {
	// Every SearchScope constant is supported by this package;
	// searchScopeSupported exists to reject a value that a caller builds from
	// a string. Exercise that path directly with an out-of-range scope.
	api := &fakeZepAPI{edges: []*zep.Edge{{Fact: zep.String("f")}}}
	svc := &memoryService{
		api:       api,
		scope:     SearchScope("not_a_real_scope"),
		graphUUID: func(*memory.SearchRequest) string { return "graph-uuid-1" },
		logger:    discardLogger(),
	}

	resp, err := svc.SearchMemory(context.Background(), &memory.SearchRequest{UserID: "u1", Query: "q"})
	if err != nil {
		t.Fatalf("SearchMemory err: %v", err)
	}
	if len(resp.Memories) != 0 {
		t.Fatalf("unsupported scope must return no memories, got %d", len(resp.Memories))
	}
	// Must short-circuit before issuing the search.
	if api.searchCalls != 0 {
		t.Fatal("unsupported scope must not issue a search")
	}
}

// TestMemoryServiceWithoutGraphUUID asserts that the memory service returns
// no memories, and issues no search, when the application configured no
// graph UUID. Zep v4 addresses a graph by UUID, and the ADK user ID is not
// one.
func TestMemoryServiceWithoutGraphUUID(t *testing.T) {
	api := &fakeZepAPI{edges: []*zep.Edge{{Fact: zep.String("f")}}}
	svc := &memoryService{api: api, scope: SearchScopeEdges, logger: discardLogger()}

	resp, err := svc.SearchMemory(context.Background(), &memory.SearchRequest{UserID: "u1", Query: "q"})
	if err != nil {
		t.Fatalf("SearchMemory err: %v", err)
	}
	if len(resp.Memories) != 0 {
		t.Fatalf("want no memories without a graph UUID, got %d", len(resp.Memories))
	}
	if api.searchCalls != 0 {
		t.Fatal("a missing graph UUID must not issue a search")
	}
}

// --- Graph search tool scope mapping ------------------------------------
//
// See also tool_test.go for the pin-or-expose merge/schema tests
// (TestGraphSearchHandler*, TestGraphSearchToolSchema*).

func TestGraphSearchToolScopeMapping(t *testing.T) {
	api := &fakeZepAPI{nodes: []*zep.Node{{Name: zep.String("Jane"), Summary: zep.String("likes hiking")}}}
	handler := newGraphSearchHandler(api,
		WithGraphUUID("graph-uuid-1"),
		WithToolSearchScope(SearchScopeNodes),
		WithToolLogger(discardLogger()))

	out, err := handler(fakeSearchToolContext{Context: context.Background(), userID: "u1"}, SearchArgs{Query: "jane"})
	if err != nil {
		t.Fatalf("handler err: %v", err)
	}
	if !equalStrings(out.Facts, []string{"Jane: likes hiking"}) {
		t.Fatalf("facts = %v, want node mapping", out.Facts)
	}
	if api.lastGraphUUID != "graph-uuid-1" {
		t.Fatalf("graph UUID = %q, want graph-uuid-1", api.lastGraphUUID)
	}
}

func TestGraphSearchToolRejectsUnsupportedScope(t *testing.T) {
	// See TestMemoryServiceRejectsUnsupportedScope.
	api := &fakeZepAPI{edges: []*zep.Edge{{Fact: zep.String("f")}}}
	handler := newGraphSearchHandler(api,
		WithGraphUUID("graph-uuid-1"),
		WithToolSearchScope(SearchScope("not_a_real_scope")),
		WithToolLogger(discardLogger()))

	out, err := handler(fakeSearchToolContext{Context: context.Background(), userID: "u1"}, SearchArgs{Query: "x"})
	if err != nil {
		t.Fatalf("handler err: %v", err)
	}
	if len(out.Facts) != 0 {
		t.Fatalf("unsupported scope must return no facts, got %v", out.Facts)
	}
	if api.searchCalls != 0 {
		t.Fatal("unsupported scope must not issue a search")
	}
}

// TestGraphSearchToolWithoutGraphUUID asserts that the tool returns no facts,
// and issues no search, when the application configured no graph UUID.
func TestGraphSearchToolWithoutGraphUUID(t *testing.T) {
	api := &fakeZepAPI{edges: []*zep.Edge{{Fact: zep.String("f")}}}
	handler := newGraphSearchHandler(api, WithToolLogger(discardLogger()))

	out, err := handler(fakeSearchToolContext{Context: context.Background(), userID: "u1"}, SearchArgs{Query: "x"})
	if err != nil {
		t.Fatalf("handler err: %v", err)
	}
	if len(out.Facts) != 0 {
		t.Fatalf("want no facts without a graph UUID, got %v", out.Facts)
	}
	if api.searchCalls != 0 {
		t.Fatal("a missing graph UUID must not issue a search")
	}
}

func TestTruncateMessageContent(t *testing.T) {
	t.Run("under limit unchanged", func(t *testing.T) {
		in := strings.Repeat("x", maxMessageContentChars)
		if got := truncateMessageContent(discardLogger(), "t", in); got != in {
			t.Fatalf("content at limit must be unchanged (len %d -> %d)", len(in), len(got))
		}
	})
	t.Run("over limit truncated to target", func(t *testing.T) {
		in := strings.Repeat("x", maxMessageContentChars+1)
		got := truncateMessageContent(discardLogger(), "t", in)
		if len(got) != messageTruncateChars {
			t.Fatalf("len = %d, want %d", len(got), messageTruncateChars)
		}
	})
	t.Run("does not split a multi-byte rune", func(t *testing.T) {
		// Each "é" is 2 bytes; build a string that overflows so the cut lands
		// mid-rune, then verify the result is valid UTF-8.
		in := strings.Repeat("é", maxMessageContentChars) // ~2x bytes
		got := truncateMessageContent(discardLogger(), "t", in)
		if len(got) > maxMessageContentChars {
			t.Fatalf("len = %d, exceeds limit", len(got))
		}
		if strings.ContainsRune(got, '�') || !validUTF8(got) {
			t.Fatal("truncation produced invalid UTF-8")
		}
	})
	t.Run("warning logs lengths only, never message content", func(t *testing.T) {
		// Matches Python's and TypeScript's truncation-warning tests: the
		// warning must carry only lengths (and the non-PII thread label) for
		// debugging, and must never leak the message body itself.
		var buf strings.Builder
		logger := slog.New(slog.NewTextHandler(&buf, nil))

		secret := strings.Repeat("S", maxMessageContentChars+2000)
		got := truncateMessageContent(logger, "thread-secret", secret)

		logged := buf.String()
		if logged == "" {
			t.Fatal("expected a warning to be logged for oversize content")
		}
		if !strings.Contains(logged, strconv.Itoa(len(secret))) {
			t.Fatalf("warning = %q, want it to contain the original length %d", logged, len(secret))
		}
		if !strings.Contains(logged, strconv.Itoa(len(got))) {
			t.Fatalf("warning = %q, want it to contain the truncated length %d", logged, len(got))
		}
		if !strings.Contains(logged, strconv.Itoa(maxMessageContentChars)) {
			t.Fatalf("warning = %q, want it to contain the limit %d", logged, maxMessageContentChars)
		}
		// The content itself (a long run of "S") must never appear in the log.
		if strings.Contains(logged, strings.Repeat("S", 20)) {
			t.Fatalf("warning leaked message content: %q", logged)
		}
	})
}

// --- helpers -------------------------------------------------------------

func entryTexts(resp *memory.SearchResponse) []string {
	var out []string
	for _, e := range resp.Memories {
		out = append(out, LastUserText(e.Content))
	}
	return out
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
