package zepadk

import (
	"context"
	"errors"
	"testing"

	"google.golang.org/adk/memory"
	"google.golang.org/adk/model"
	"google.golang.org/genai"
)

func TestLastUserText(t *testing.T) {
	tests := []struct {
		name    string
		content *genai.Content
		want    string
	}{
		{name: "nil content", content: nil, want: ""},
		{name: "no parts", content: &genai.Content{}, want: ""},
		{
			name:    "single text part",
			content: genai.NewContentFromText("hello", genai.RoleUser),
			want:    "hello",
		},
		{
			name: "returns last text part",
			content: &genai.Content{Parts: []*genai.Part{
				genai.NewPartFromText("first"),
				genai.NewPartFromText("second"),
			}},
			want: "second",
		},
		{
			name: "skips trailing empty/nil parts",
			content: &genai.Content{Parts: []*genai.Part{
				genai.NewPartFromText("real"),
				{Text: ""},
				nil,
			}},
			want: "real",
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := LastUserText(tc.content); got != tc.want {
				t.Fatalf("LastUserText() = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestInjectSystemInstruction(t *testing.T) {
	t.Run("nil request is safe", func(t *testing.T) {
		InjectSystemInstruction(nil, "ignored") // must not panic
	})

	t.Run("empty text is a no-op", func(t *testing.T) {
		req := &model.LLMRequest{}
		InjectSystemInstruction(req, "")
		if req.Config != nil {
			t.Fatalf("expected Config to remain nil for empty text, got %+v", req.Config)
		}
	})

	t.Run("allocates config and instruction", func(t *testing.T) {
		req := &model.LLMRequest{}
		InjectSystemInstruction(req, "remember this")
		if req.Config == nil || req.Config.SystemInstruction == nil {
			t.Fatal("expected SystemInstruction to be allocated")
		}
		if got := LastUserText(req.Config.SystemInstruction); got != "remember this" {
			t.Fatalf("system instruction = %q, want %q", got, "remember this")
		}
	})

	t.Run("appends to existing instruction", func(t *testing.T) {
		req := &model.LLMRequest{Config: &genai.GenerateContentConfig{
			SystemInstruction: genai.NewContentFromText("base", genai.RoleUser),
		}}
		InjectSystemInstruction(req, "extra")
		parts := req.Config.SystemInstruction.Parts
		if len(parts) != 2 {
			t.Fatalf("expected 2 parts, got %d", len(parts))
		}
		if parts[0].Text != "base" || parts[1].Text != "extra" {
			t.Fatalf("unexpected parts: %q, %q", parts[0].Text, parts[1].Text)
		}
	})
}

// --- CreateUser / CreateThread ---------------------------------------------
//
// These exercise the seam-friendly cores (createUserWithAPI /
// createThreadWithAPI) with a fakeZepAPI, so the UUID results and the failure
// paths can be tested without a live Zep account.

func TestCreateUserAndThreadNilClient(t *testing.T) {
	ctx := context.Background()
	userUUID, graphUUID, err := CreateUser(ctx, nil, "u1", "Jane", "Smith", "jane@example.com")
	if userUUID != "" || graphUUID != "" || err != nil {
		t.Fatalf("CreateUser(nil client) = (%q, %q, %v), want empty values and nil", userUUID, graphUUID, err)
	}
	threadUUID, err := CreateThread(ctx, nil, "t1", "user-uuid-1")
	if threadUUID != "" || err != nil {
		t.Fatalf("CreateThread(nil client) = (%q, %v), want empty value and nil", threadUUID, err)
	}
}

func TestCreateUserReturnsUUIDs(t *testing.T) {
	api := &fakeZepAPI{}
	userUUID, graphUUID, err := createUserWithAPI(context.Background(), api, "u1", "Jane", "Smith", "jane@example.com")
	if err != nil {
		t.Fatalf("createUserWithAPI err = %v", err)
	}
	if userUUID != "user-uuid-1" || graphUUID != "graph-uuid-1" {
		t.Fatalf("createUserWithAPI = (%q, %q), want the UUIDs from the response", userUUID, graphUUID)
	}
	if api.createUserCalls != 1 {
		t.Fatalf("CreateUser calls = %d, want 1", api.createUserCalls)
	}
	req := api.lastCreateUserReq
	if req == nil {
		t.Fatal("CreateUser was not called with a request")
	}
	if req.UserID == nil || *req.UserID != "u1" {
		t.Fatalf("UserID = %v, want u1", req.UserID)
	}
	if req.FirstName == nil || *req.FirstName != "Jane" {
		t.Fatalf("FirstName = %v, want Jane", req.FirstName)
	}
	if req.LastName == nil || *req.LastName != "Smith" {
		t.Fatalf("LastName = %v, want Smith", req.LastName)
	}
	if req.Email == nil || *req.Email != "jane@example.com" {
		t.Fatalf("Email = %v, want jane@example.com", req.Email)
	}
}

func TestCreateUserPropagatesError(t *testing.T) {
	wantErr := errors.New("boom")
	api := &fakeZepAPI{createUserErr: wantErr}
	userUUID, graphUUID, err := createUserWithAPI(context.Background(), api, "u1", "Jane", "Smith", "")
	if userUUID != "" || graphUUID != "" {
		t.Fatalf("createUserWithAPI = (%q, %q), want empty values on error", userUUID, graphUUID)
	}
	if !errors.Is(err, wantErr) {
		t.Fatalf("err = %v, want %v", err, wantErr)
	}
}

func TestCreateThreadReturnsUUID(t *testing.T) {
	api := &fakeZepAPI{}
	threadUUID, err := createThreadWithAPI(context.Background(), api, "t1", "user-uuid-1")
	if err != nil {
		t.Fatalf("createThreadWithAPI err = %v", err)
	}
	if threadUUID != "thread-uuid-1" {
		t.Fatalf("threadUUID = %q, want thread-uuid-1", threadUUID)
	}
	if api.createThreadCalls != 1 {
		t.Fatalf("CreateThread calls = %d, want 1", api.createThreadCalls)
	}
	req := api.lastCreateThreadReq
	if req == nil {
		t.Fatal("CreateThread was not called with a request")
	}
	if req.ThreadID == nil || *req.ThreadID != "t1" {
		t.Fatalf("ThreadID = %v, want t1", req.ThreadID)
	}
	if req.UserUUID != "user-uuid-1" {
		t.Fatalf("UserUUID = %q, want user-uuid-1", req.UserUUID)
	}
}

func TestCreateThreadPropagatesError(t *testing.T) {
	wantErr := errors.New("boom")
	api := &fakeZepAPI{createThreadErr: wantErr}
	threadUUID, err := createThreadWithAPI(context.Background(), api, "t1", "user-uuid-1")
	if threadUUID != "" {
		t.Fatalf("threadUUID = %q, want empty on error", threadUUID)
	}
	if !errors.Is(err, wantErr) {
		t.Fatalf("err = %v, want %v", err, wantErr)
	}
}

// TestCreateThreadWithoutUserUUID asserts that the helper makes no call when
// the application supplies no user UUID. Zep v4 attaches a thread to a user
// by UUID.
func TestCreateThreadWithoutUserUUID(t *testing.T) {
	threadUUID, err := CreateThread(context.Background(), nil, "t1", "")
	if threadUUID != "" || err != nil {
		t.Fatalf("CreateThread without a user UUID = (%q, %v), want empty value and nil", threadUUID, err)
	}
}

func TestNewBeforeModelCallbackNilClient(t *testing.T) {
	// A nil client must produce a callback that is a no-op and never touches
	// the (here nil) CallbackContext.
	cb := NewBeforeModelCallback(nil)
	req := &model.LLMRequest{}
	resp, err := cb(nil, req)
	if err != nil {
		t.Fatalf("callback err = %v, want nil", err)
	}
	if resp != nil {
		t.Fatalf("callback resp = %+v, want nil (proceed to model)", resp)
	}
	if req.Config != nil {
		t.Fatalf("nil client must not mutate the request, got Config = %+v", req.Config)
	}
}

func TestResolveCallbackOptions(t *testing.T) {
	t.Run("defaults", func(t *testing.T) {
		o := resolveCallbackOptions(nil)
		if o.contextTemplate != DefaultContextTemplate {
			t.Fatalf("contextTemplate = %q, want default", o.contextTemplate)
		}
		if o.logger == nil {
			t.Fatal("logger must default to a non-nil logger")
		}
		if o.userName != "" {
			t.Fatalf("userName = %q, want empty", o.userName)
		}
		if o.contextBuilder != nil {
			t.Fatal("contextBuilder must default to nil")
		}
	})

	t.Run("overrides", func(t *testing.T) {
		o := resolveCallbackOptions([]CallbackOption{
			WithContextTemplate("CUSTOM: {context}"),
			WithUserMessageName("Jane"),
			WithLogger(nil), // nil logger must be ignored
		})
		if o.contextTemplate != "CUSTOM: {context}" {
			t.Fatalf("contextTemplate = %q, want CUSTOM: {context}", o.contextTemplate)
		}
		if o.userName != "Jane" {
			t.Fatalf("userName = %q, want Jane", o.userName)
		}
		if o.logger == nil {
			t.Fatal("WithLogger(nil) must not unset the default logger")
		}
	})

	t.Run("WithContextPrefix shim sets an equivalent template", func(t *testing.T) {
		o := resolveCallbackOptions([]CallbackOption{WithContextPrefix("PREFIX: ")})
		if o.contextTemplate != "PREFIX: {context}" {
			t.Fatalf("contextTemplate = %q, want %q", o.contextTemplate, "PREFIX: {context}")
		}
	})
}

func TestMemoryServiceNilClient(t *testing.T) {
	svc := NewMemoryService(nil)
	ctx := context.Background()

	if err := svc.AddSessionToMemory(ctx, nil); err != nil {
		t.Fatalf("AddSessionToMemory = %v, want nil", err)
	}

	resp, err := svc.SearchMemory(ctx, &memory.SearchRequest{UserID: "u1", Query: "anything"})
	if err != nil {
		t.Fatalf("SearchMemory err = %v, want nil", err)
	}
	if resp == nil || len(resp.Memories) != 0 {
		t.Fatalf("SearchMemory = %+v, want empty response", resp)
	}
}

func TestMemoryServiceSearchGuards(t *testing.T) {
	// Even with a non-nil (unused) client, empty UserID/Query short-circuit
	// before any network call.
	svc := NewMemoryService(NewClient("test-key"))
	ctx := context.Background()

	tests := []struct {
		name string
		req  *memory.SearchRequest
	}{
		{name: "nil request", req: nil},
		{name: "empty user", req: &memory.SearchRequest{Query: "q"}},
		{name: "empty query", req: &memory.SearchRequest{UserID: "u1"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			resp, err := svc.SearchMemory(ctx, tc.req)
			if err != nil {
				t.Fatalf("SearchMemory err = %v, want nil", err)
			}
			if resp == nil || len(resp.Memories) != 0 {
				t.Fatalf("SearchMemory = %+v, want empty response", resp)
			}
		})
	}
}

func TestMemoryServiceOptions(t *testing.T) {
	svc, ok := NewMemoryService(nil,
		WithSearchScope(SearchScopeNodes),
		WithSearchLimit(5),
		WithMemoryLogger(nil),
	).(*memoryService)
	if !ok {
		t.Fatal("NewMemoryService did not return *memoryService")
	}
	if svc.scope != SearchScopeNodes {
		t.Fatalf("scope = %q, want nodes", svc.scope)
	}
	if svc.limit == nil || *svc.limit != 5 {
		t.Fatalf("limit = %v, want 5", svc.limit)
	}
	if svc.logger == nil {
		t.Fatal("WithMemoryLogger(nil) must not unset the default logger")
	}
}

func TestNewGraphSearchTool(t *testing.T) {
	t.Run("defaults", func(t *testing.T) {
		tl, err := NewGraphSearchTool(nil)
		if err != nil {
			t.Fatalf("NewGraphSearchTool err = %v", err)
		}
		if tl == nil {
			t.Fatal("expected a non-nil tool")
		}
		if tl.Name() != DefaultGraphSearchToolName {
			t.Fatalf("Name() = %q, want %q", tl.Name(), DefaultGraphSearchToolName)
		}
		if tl.Description() != DefaultGraphSearchToolDescription {
			t.Fatalf("Description() = %q, want default", tl.Description())
		}
	})

	t.Run("overrides", func(t *testing.T) {
		tl, err := NewGraphSearchTool(nil,
			WithToolName("recall"),
			WithToolDescription("custom"),
			WithGraphUUID("graph-uuid-1"),
			WithToolSearchScope(SearchScopeAuto),
			WithToolSearchLimit(3),
			WithToolLogger(nil),
		)
		if err != nil {
			t.Fatalf("NewGraphSearchTool err = %v", err)
		}
		if tl.Name() != "recall" {
			t.Fatalf("Name() = %q, want recall", tl.Name())
		}
		if tl.Description() != "custom" {
			t.Fatalf("Description() = %q, want custom", tl.Description())
		}
	})

	t.Run("empty overrides keep defaults", func(t *testing.T) {
		tl, err := NewGraphSearchTool(nil, WithToolName(""), WithToolDescription(""))
		if err != nil {
			t.Fatalf("NewGraphSearchTool err = %v", err)
		}
		if tl.Name() != DefaultGraphSearchToolName {
			t.Fatalf("empty WithToolName must keep default, got %q", tl.Name())
		}
	})
}

func TestNewClientFromEnv(t *testing.T) {
	t.Setenv("ZEP_API_KEY", "")
	if c := NewClientFromEnv(); c != nil {
		t.Fatal("NewClientFromEnv() with empty key must return nil")
	}
	t.Setenv("ZEP_API_KEY", "test-key")
	if c := NewClientFromEnv(); c == nil {
		t.Fatal("NewClientFromEnv() with a key must return a client")
	}
}
