package zepadk

import (
	"context"
	"os"
	"testing"
	"time"

	zep "github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"

	"google.golang.org/adk/memory"
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

func TestLiveEnsureAndPersist(t *testing.T) {
	client := requireLiveClient(t)
	ctx := context.Background()

	suffix := time.Now().UTC().Format("20060102150405")
	userID := "zepadk-live-user-" + suffix
	threadID := "zepadk-live-thread-" + suffix

	if err := EnsureUser(ctx, client, userID, "Live", "Tester", "live-"+suffix+"@example.com"); err != nil {
		t.Fatalf("EnsureUser: %v", err)
	}
	// A second call must be idempotent (409 swallowed).
	if err := EnsureUser(ctx, client, userID, "Live", "Tester", ""); err != nil {
		t.Fatalf("EnsureUser (idempotent): %v", err)
	}
	if err := EnsureThread(ctx, client, threadID, userID); err != nil {
		t.Fatalf("EnsureThread: %v", err)
	}

	// Persist a user turn and request the Context Block in one round-trip.
	resp, err := client.Thread.AddMessages(ctx, threadID, &zep.AddThreadMessagesRequest{
		ReturnContext: zep.Bool(true),
		Messages: []*zep.Message{{
			Role:    zep.RoleTypeUserRole,
			Content: "My name is Live Tester and my favorite language is Go.",
		}},
	})
	if err != nil {
		t.Fatalf("AddMessages: %v", err)
	}
	if resp == nil {
		t.Fatal("AddMessages returned a nil response")
		return
	}
	t.Logf("Context Block present: %v", resp.Context != nil)
}

func TestLiveMemoryServiceSearch(t *testing.T) {
	client := requireLiveClient(t)
	ctx := context.Background()

	// Search against a user that almost certainly has no graph; the call must
	// still succeed and return a (possibly empty) response without error.
	svc := NewMemoryService(client)
	resp, err := svc.SearchMemory(ctx, &memory.SearchRequest{
		UserID: "zepadk-live-user-nonexistent-" + time.Now().UTC().Format("20060102150405"),
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
