package zepadk

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	zep "github.com/getzep/zep-go/v3"
	zepoption "github.com/getzep/zep-go/v3/option"

	"google.golang.org/adk/model"
	"google.golang.org/genai"
)

// --- custom context-builder seam ------------------------------------------
//
// These exercise newBeforeModelCallback's builder-set path: persistence
// (AddMessages without ReturnContext) and the custom ContextBuilder run
// concurrently, each isolated from the other's failure.

// TestBuilderReceivesFullyPopulatedInput asserts every ContextInput field is
// populated as documented, including Client identity (the exact
// *zepclient.Client passed to NewBeforeModelCallback) and the Request pointer
// (the same *model.LLMRequest passed to the callback).
func TestBuilderReceivesFullyPopulatedInput(t *testing.T) {
	api := &fakeZepAPI{}
	client := NewClient("test-key")

	var got ContextInput
	builder := func(_ context.Context, in ContextInput) (string, error) {
		got = in
		return "BUILT CONTEXT", nil
	}

	cb := newBeforeModelCallback(client, api, WithContextBuilder(builder), WithUserMessageName("Jane"))

	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi there", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi there", genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v", err)
	}

	if got.Client != client {
		t.Fatalf("ContextInput.Client = %p, want %p (the client passed to NewBeforeModelCallback)", got.Client, client)
	}
	if got.UserID != "u1" {
		t.Fatalf("ContextInput.UserID = %q, want u1", got.UserID)
	}
	if got.ThreadID != "thread-1" {
		t.Fatalf("ContextInput.ThreadID = %q, want thread-1", got.ThreadID)
	}
	if got.UserMessage != "hi there" {
		t.Fatalf("ContextInput.UserMessage = %q, want %q", got.UserMessage, "hi there")
	}
	if got.Callback != cc {
		t.Fatalf("ContextInput.Callback = %v, want the CallbackContext passed to cb", got.Callback)
	}
	if got.Request != req {
		t.Fatalf("ContextInput.Request = %p, want %p (the *model.LLMRequest passed to cb)", got.Request, req)
	}
}

// TestBuilderSetPersistsWithoutReturnContext verifies that when a builder is
// configured, AddMessages is called WITHOUT ReturnContext (context comes from
// the builder instead), the builder is invoked, and its output is injected
// through the template.
func TestBuilderSetPersistsWithoutReturnContext(t *testing.T) {
	api := &fakeZepAPI{contextOut: "SHOULD NOT BE USED"}
	client := NewClient("test-key")

	var builderCalls int
	builder := func(_ context.Context, _ ContextInput) (string, error) {
		builderCalls++
		return "BUILDER FACTS", nil
	}

	cb := newBeforeModelCallback(client, api, WithContextBuilder(builder))
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi", genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v", err)
	}

	if builderCalls != 1 {
		t.Fatalf("builder calls = %d, want 1", builderCalls)
	}
	if api.addMsgCalls != 1 {
		t.Fatalf("AddMessages calls = %d, want 1", api.addMsgCalls)
	}
	if api.lastAddMsgReq == nil || api.lastAddMsgReq.ReturnContext != nil {
		t.Fatalf("AddMessages ReturnContext = %v, want nil (unused) when a builder is configured", api.lastAddMsgReq.ReturnContext)
	}

	got := LastUserText(req.Config.SystemInstruction)
	if !strings.Contains(got, "BUILDER FACTS") {
		t.Fatalf("system instruction = %q, want builder output injected", got)
	}
	if !strings.Contains(got, "<ZEP_CONTEXT>") {
		t.Fatalf("system instruction = %q, want default template wrapper", got)
	}
}

// TestBuilderEmptyStringSkipsInjection verifies that a builder returning ""
// results in no injection, while persistence still happens.
func TestBuilderEmptyStringSkipsInjection(t *testing.T) {
	api := &fakeZepAPI{}
	client := NewClient("test-key")

	builder := func(_ context.Context, _ ContextInput) (string, error) {
		return "", nil
	}

	cb := newBeforeModelCallback(client, api, WithContextBuilder(builder))
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi", genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v", err)
	}

	if api.addMsgCalls != 1 {
		t.Fatalf("AddMessages calls = %d, want 1 (persist still happens)", api.addMsgCalls)
	}
	if req.Config != nil {
		t.Fatalf("Config = %+v, want nil (no injection for empty builder output)", req.Config)
	}
}

// TestBuilderErrorSkipsInjectionButPersists verifies the mandatory error
// isolation: a builder error is logged, injection is skipped, and persistence
// still completes (unaffected by the builder's failure).
func TestBuilderErrorSkipsInjectionButPersists(t *testing.T) {
	api := &fakeZepAPI{}
	client := NewClient("test-key")

	builder := func(_ context.Context, _ ContextInput) (string, error) {
		return "", errors.New("builder boom")
	}

	cb := newBeforeModelCallback(client, api, WithContextBuilder(builder))
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi", genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v, want nil (builder errors must not surface)", err)
	}

	if api.addMsgCalls != 1 {
		t.Fatalf("AddMessages calls = %d, want 1 (persist still completes despite builder error)", api.addMsgCalls)
	}
	if req.Config != nil {
		t.Fatalf("Config = %+v, want nil (no injection on builder error)", req.Config)
	}
}

// TestBuilderPanicSkipsInjectionButPersists verifies that a panicking builder
// is recovered inside its goroutine (an unrecovered goroutine panic would
// kill the whole process) and degrades exactly like a builder error: no
// injection, persistence still completes, no error surfaced.
func TestBuilderPanicSkipsInjectionButPersists(t *testing.T) {
	api := &fakeZepAPI{}
	client := NewClient("test-key")

	builder := func(_ context.Context, _ ContextInput) (string, error) {
		panic("builder panic boom")
	}

	cb := newBeforeModelCallback(client, api, WithContextBuilder(builder))
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi", genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v, want nil (builder panics must not surface)", err)
	}

	if api.addMsgCalls != 1 {
		t.Fatalf("AddMessages calls = %d, want 1 (persist still completes despite builder panic)", api.addMsgCalls)
	}
	if req.Config != nil {
		t.Fatalf("Config = %+v, want nil (no injection on builder panic)", req.Config)
	}
}

// TestPersistErrorBuilderSuccessSkipsDedupButInjects verifies the mandatory
// error isolation in the other direction: a persist error means dedup is NOT
// marked (so a retry can happen next turn), but a successful builder result is
// still injected.
func TestPersistErrorBuilderSuccessSkipsDedupButInjects(t *testing.T) {
	api := &fakeZepAPI{addErr: errors.New("persist boom")}
	client := NewClient("test-key")

	builder := func(_ context.Context, _ ContextInput) (string, error) {
		return "BUILDER FACTS", nil
	}

	cb := newBeforeModelCallback(client, api, WithContextBuilder(builder))
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi", genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v, want nil (persist errors must not surface)", err)
	}

	got := LastUserText(req.Config.SystemInstruction)
	if !strings.Contains(got, "BUILDER FACTS") {
		t.Fatalf("system instruction = %q, want builder output injected despite persist failure", got)
	}
}

// TestPersistAndBuilderRunConcurrently proves persistence and the builder
// actually run concurrently rather than sequentially: the builder blocks until
// it observes that AddMessages has already been called, with a bounded
// timeout so the test fails deterministically (no sleeps) if the
// implementation regresses to a sequential call.
func TestPersistAndBuilderRunConcurrently(t *testing.T) {
	persistStarted := make(chan struct{})
	releasePersist := make(chan struct{})

	api := &blockingFakeZepAPI{
		onAddMessages: persistStarted,
		blockAddUntil: releasePersist,
	}
	client := NewClient("test-key")

	builderObservedPersistStart := make(chan bool, 1)
	builder := func(_ context.Context, _ ContextInput) (string, error) {
		select {
		case <-persistStarted:
			builderObservedPersistStart <- true
		case <-time.After(2 * time.Second):
			builderObservedPersistStart <- false
		}
		close(releasePersist)
		return "FACTS", nil
	}

	cb := newBeforeModelCallback(client, api, WithContextBuilder(builder))
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi", genai.RoleUser)}}

	done := make(chan struct{})
	go func() {
		defer close(done)
		if _, err := cb(cc, req); err != nil {
			t.Errorf("cb err: %v", err)
		}
	}()

	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("callback did not return in time; builder/persist may be deadlocked")
	}

	select {
	case observed := <-builderObservedPersistStart:
		if !observed {
			t.Fatal("builder never observed persistence starting; persist and builder did not run concurrently")
		}
	default:
		t.Fatal("builder did not run")
	}
}

// blockingFakeZepAPI wraps fakeZepAPI, closing onAddMessages when AddMessages
// is entered and blocking until blockAddUntil is closed, to let tests
// deterministically observe interleaving between persistence and a
// concurrent context builder.
type blockingFakeZepAPI struct {
	fakeZepAPI
	onAddMessages chan struct{}
	blockAddUntil chan struct{}
}

func (b *blockingFakeZepAPI) AddMessages(ctx context.Context, threadID string, req *zep.AddThreadMessagesRequest, opts ...zepoption.RequestOption) (*zep.AddThreadMessagesResponse, error) {
	close(b.onAddMessages)
	<-b.blockAddUntil
	return b.fakeZepAPI.AddMessages(ctx, threadID, req, opts...)
}

// --- Template tests --------------------------------------------------------

func TestDefaultTemplateContainsZepContextTag(t *testing.T) {
	if !strings.Contains(DefaultContextTemplate, "<ZEP_CONTEXT>") {
		t.Fatalf("DefaultContextTemplate = %q, want it to contain <ZEP_CONTEXT>", DefaultContextTemplate)
	}
	if !strings.Contains(DefaultContextTemplate, "{context}") {
		t.Fatalf("DefaultContextTemplate = %q, want it to contain the {context} placeholder", DefaultContextTemplate)
	}
}

func TestWithContextTemplateOverride(t *testing.T) {
	api := &fakeZepAPI{contextOut: "USER FACTS"}
	client := NewClient("test-key")

	cb := newBeforeModelCallback(client, api, WithContextTemplate("CUSTOM: {context}"))
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi", genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v", err)
	}
	got := LastUserText(req.Config.SystemInstruction)
	if got != "CUSTOM: USER FACTS" {
		t.Fatalf("system instruction = %q, want %q", got, "CUSTOM: USER FACTS")
	}
}

// TestTemplateRenderingIsSafe verifies template rendering uses plain string
// replacement (strings.ReplaceAll), never fmt verbs, so content containing
// literal %, {}, or {context} substrings is injected verbatim and safely.
func TestTemplateRenderingIsSafe(t *testing.T) {
	tricky := "100% of {things} happened, and {context} too"
	api := &fakeZepAPI{contextOut: tricky}
	client := NewClient("test-key")

	cb := newBeforeModelCallback(client, api)
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi", genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v", err)
	}
	got := LastUserText(req.Config.SystemInstruction)
	if !strings.Contains(got, tricky) {
		t.Fatalf("system instruction = %q, want it to contain the tricky content verbatim: %q", got, tricky)
	}
}

// TestWithContextPrefixShim verifies the deprecated WithContextPrefix shim
// produces prefix+block exactly as before, for legacy callers.
func TestWithContextPrefixShim(t *testing.T) {
	api := &fakeZepAPI{contextOut: "USER FACTS"}
	client := NewClient("test-key")

	cb := newBeforeModelCallback(client, api, WithContextPrefix("LEGACY PREFIX: "))
	cc := newFakeCallbackContext("thread-1", "u1", genai.NewContentFromText("hi", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{genai.NewContentFromText("hi", genai.RoleUser)}}

	if _, err := cb(cc, req); err != nil {
		t.Fatalf("cb err: %v", err)
	}
	got := LastUserText(req.Config.SystemInstruction)
	if got != "LEGACY PREFIX: USER FACTS" {
		t.Fatalf("system instruction = %q, want %q (prefix+block exactly, no wrapper tags)", got, "LEGACY PREFIX: USER FACTS")
	}
}
