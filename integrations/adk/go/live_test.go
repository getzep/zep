package zepadk

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	zepclient "github.com/getzep/zep-go/v4/client"

	"google.golang.org/adk/memory"
	"google.golang.org/adk/model"
	"google.golang.org/genai"
)

// These tests exercise the integration against the live Zep API. They are
// skipped unless ZEP_API_KEY is set, so the default `go test ./...` run remains
// fully offline. Run them with:
//
//	ZEP_API_KEY=... go test -run TestLive -v ./...
//
// Zep ingestion is asynchronous, so these assert on the API round-trips
// succeeding (and identity-resolution facts becoming available) rather than on
// instant recall of the just-added message.

func requireLiveClient(t *testing.T) *zepclient.Client {
	t.Helper()
	if os.Getenv("ZEP_API_KEY") == "" {
		t.Skip("ZEP_API_KEY not set; skipping live test")
	}
	c := NewClientFromEnv()
	if c == nil {
		t.Fatal("NewClientFromEnv returned nil despite ZEP_API_KEY being set")
	}
	return c
}

// deleteLiveUser is a best-effort cleanup helper: it deletes the user (which
// cascades to its threads on the Zep side), logging rather than failing the
// test if cleanup itself errors. Mirrors the finally-cleanup in the Python and
// TypeScript live suites. Zep v4 deletes a user by UUID.
func deleteLiveUser(t *testing.T, client *zepclient.Client, userUUID string) {
	t.Helper()
	if client == nil || userUUID == "" {
		return
	}
	if _, err := client.User.Delete(context.Background(), userUUID); err != nil {
		t.Logf("cleanup: failed to delete live test user %q: %v", userUUID, err)
	}
}

// TestLiveCreateAndPersist drives the integration's own NewBeforeModelCallback
// and NewAfterModelCallback against the live Zep API -- not the raw SDK -- so
// this test exercises exactly the code path a real agent runs: create the
// user and the thread, persist the user turn, inject the returned context
// block into the system instruction, then persist the reply of the assistant
// to the same thread.
func TestLiveCreateAndPersist(t *testing.T) {
	t.Skip("ZEPAI-3605: a thread of a user that has no user_id cannot receive a message; the API returns 404 until the fix is deployed")

	client := requireLiveClient(t)
	ctx := context.Background()

	suffix := time.Now().UTC().Format("20060102150405")
	adkUserID := "zepadk-live-user-" + suffix
	adkSessionID := "zepadk-live-thread-" + suffix

	userUUID, graphUUID, err := CreateUser(ctx, client, "", "Live", "Tester", "live-"+suffix+"@example.com")
	if err != nil {
		t.Fatalf("CreateUser: %v", err)
	}
	t.Cleanup(func() { deleteLiveUser(t, client, userUUID) })
	if userUUID == "" || graphUUID == "" {
		t.Fatalf("CreateUser returned user_uuid=%q graph_uuid=%q, want both", userUUID, graphUUID)
	}

	threadUUID, err := CreateThread(ctx, client, "", userUUID)
	if err != nil {
		t.Fatalf("CreateThread: %v", err)
	}
	if threadUUID == "" {
		t.Fatal("CreateThread returned an empty thread UUID")
	}

	// --- Drive the real before/after callbacks against the live client ----

	beforeCB := NewBeforeModelCallback(client,
		WithThreadUUID(threadUUID),
		WithUserUUID(userUUID),
		WithUserMessageName("Live Tester"))
	afterCB := NewAfterModelCallback(client,
		WithAfterThreadUUID(threadUUID),
		WithAssistantMessageName("assistant"))

	cc := newFakeCallbackContext(adkSessionID, adkUserID,
		genai.NewContentFromText("My name is Live Tester and my favorite language is Go.", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{
		genai.NewContentFromText("My name is Live Tester and my favorite language is Go.", genai.RoleUser),
	}}

	resp, err := beforeCB(cc, req)
	if err != nil {
		t.Fatalf("NewBeforeModelCallback: %v", err)
	}
	if resp != nil {
		t.Fatalf("before-model callback returned a response %+v, want nil (pass-through)", resp)
	}
	if req.Config != nil && req.Config.SystemInstruction != nil {
		got := LastUserText(req.Config.SystemInstruction)
		t.Logf("system instruction after before-callback: %q", got)
		if !strings.Contains(got, "<ZEP_CONTEXT>") {
			t.Fatalf("system instruction = %q, want it to contain <ZEP_CONTEXT> when context is injected", got)
		}
	} else {
		t.Log("no context block was injected (Zep may not have returned one yet); continuing")
	}

	// Persist the reply of the assistant to the same thread through the real
	// after-model callback.
	afterResp, err := afterCB(cc, &model.LLMResponse{
		Content: genai.NewContentFromText("Nice to meet you, Live Tester!", genai.RoleModel),
	}, nil)
	if err != nil {
		t.Fatalf("NewAfterModelCallback: %v", err)
	}
	if afterResp != nil {
		t.Fatalf("after-model callback returned a response %+v, want nil (pass-through)", afterResp)
	}
}

// TestLiveGraphSearchTool exercises NewGraphSearchTool's handler against the
// live Zep API. Ingestion is asynchronous, so this only asserts the call
// round-trips successfully and returns a well-formed (possibly empty) result,
// not that a specific fact is found.
func TestLiveGraphSearchTool(t *testing.T) {
	client := requireLiveClient(t)
	ctx := context.Background()

	suffix := time.Now().UTC().Format("20060102150405")
	adkUserID := "zepadk-live-search-user-" + suffix

	userUUID, graphUUID, err := CreateUser(ctx, client, "", "Live", "Searcher", "")
	if err != nil {
		t.Fatalf("CreateUser: %v", err)
	}
	t.Cleanup(func() { deleteLiveUser(t, client, userUUID) })

	api := newZepAPI(client)
	handler := newGraphSearchHandler(api, WithGraphUUID(graphUUID))

	result, err := handler(fakeSearchToolContext{Context: ctx, userID: adkUserID}, SearchArgs{
		Query: "favorite programming language",
	})
	if err != nil {
		t.Fatalf("graph search handler returned error (should degrade gracefully): %v", err)
	}
	t.Logf("facts returned: %d", len(result.Facts))
}

func TestLiveMemoryServiceSearch(t *testing.T) {
	client := requireLiveClient(t)
	ctx := context.Background()

	suffix := time.Now().UTC().Format("20060102150405")
	userUUID, graphUUID, err := CreateUser(ctx, client, "", "Live", "Memory", "")
	if err != nil {
		t.Fatalf("CreateUser: %v", err)
	}
	t.Cleanup(func() { deleteLiveUser(t, client, userUUID) })

	// The graph of a new user is empty. The call must still succeed and
	// return a (possibly empty) response without an error.
	svc := NewMemoryService(client, WithMemoryGraphUUID(graphUUID))
	resp, err := svc.SearchMemory(ctx, &memory.SearchRequest{
		UserID: "zepadk-live-memory-user-" + suffix,
		Query:  "favorite programming language",
	})
	if err != nil {
		t.Fatalf("SearchMemory returned error (should degrade gracefully): %v", err)
	}
	if resp == nil {
		t.Fatal("SearchMemory returned a nil response")
		return
	}
	t.Logf("memory entries returned: %d", len(resp.Memories))
}
