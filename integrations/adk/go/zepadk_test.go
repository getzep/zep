package zepadk

import (
	"context"
	"errors"
	"testing"

	zep "github.com/getzep/zep-go/v3"
	zepcore "github.com/getzep/zep-go/v3/core"

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

func TestIsAlreadyExists(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{name: "nil", err: nil, want: false},
		{name: "unrelated error", err: errors.New("boom"), want: false},
		{name: "conflict error", err: &zep.ConflictError{APIError: zepcore.NewAPIError(409, nil, errors.New("exists"))}, want: true},
		{name: "api error 409", err: zepcore.NewAPIError(409, nil, errors.New("conflict")), want: true},
		{name: "api error 500", err: zepcore.NewAPIError(500, nil, errors.New("server")), want: false},
		{name: "wrapped conflict", err: errors.Join(errors.New("ctx"), &zep.ConflictError{APIError: zepcore.NewAPIError(409, nil, nil)}), want: true},
		// Zep returns HTTP 400 (not 409) for a duplicate user; the message
		// carries the signal. Verified against the live API.
		{name: "bad request user already exists", err: zepcore.NewAPIError(400, nil, errors.New(`{"message":"bad request: user already exists with user_id: u1"}`)), want: true},
		{name: "bad request other", err: zepcore.NewAPIError(400, nil, errors.New(`{"message":"bad request: invalid email"}`)), want: false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := isAlreadyExists(tc.err); got != tc.want {
				t.Fatalf("isAlreadyExists() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestEnsureUserAndThreadNilClient(t *testing.T) {
	ctx := context.Background()
	if err := EnsureUser(ctx, nil, "u1", "Jane", "Smith", "jane@example.com"); err != nil {
		t.Fatalf("EnsureUser(nil client) = %v, want nil", err)
	}
	if err := EnsureThread(ctx, nil, "t1", "u1"); err != nil {
		t.Fatalf("EnsureThread(nil client) = %v, want nil", err)
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
		if o.contextPrefix != DefaultContextPrefix {
			t.Fatalf("contextPrefix = %q, want default", o.contextPrefix)
		}
		if o.logger == nil {
			t.Fatal("logger must default to a non-nil logger")
		}
		if o.userName != "" {
			t.Fatalf("userName = %q, want empty", o.userName)
		}
	})

	t.Run("overrides", func(t *testing.T) {
		o := resolveCallbackOptions([]CallbackOption{
			WithContextPrefix("PREFIX:"),
			WithUserMessageName("Jane"),
			WithLogger(nil), // nil logger must be ignored
		})
		if o.contextPrefix != "PREFIX:" {
			t.Fatalf("contextPrefix = %q, want PREFIX:", o.contextPrefix)
		}
		if o.userName != "Jane" {
			t.Fatalf("userName = %q, want Jane", o.userName)
		}
		if o.logger == nil {
			t.Fatal("WithLogger(nil) must not unset the default logger")
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
		WithSearchScope(zep.GraphSearchScopeNodes),
		WithSearchLimit(5),
		WithMemoryLogger(nil),
	).(*memoryService)
	if !ok {
		t.Fatal("NewMemoryService did not return *memoryService")
	}
	if svc.scope != zep.GraphSearchScopeNodes {
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
			WithGraphID("graph-1"),
			WithToolSearchScope(zep.GraphSearchScopeAuto),
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
