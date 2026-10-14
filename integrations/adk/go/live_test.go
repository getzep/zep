package zepadk

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"

	zepclient "github.com/getzep/zep-go/v4/client"
	"google.golang.org/adk/memory"
	"google.golang.org/adk/model"
	"google.golang.org/genai"
)

func requireLiveClient(t *testing.T) *zepclient.Client {
	t.Helper()
	if os.Getenv("ZEP_API_KEY") == "" {
		t.Skip("ZEP_API_KEY not set; skipping live test")
	}
	client := NewClientFromEnv()
	if client == nil {
		t.Fatal("NewClientFromEnv returned nil despite ZEP_API_KEY being set")
	}
	return client
}

func createLiveUser(t *testing.T, client *zepclient.Client) (string, string) {
	t.Helper()
	ctx := context.Background()
	suffix := fmt.Sprintf("%d", time.Now().UnixNano())
	userUUID, graphUUID, err := CreateUser(ctx, client, "Live", "Tester", "live-"+suffix+"@example.com")
	if err != nil {
		t.Fatalf("CreateUser: %v", err)
	}
	t.Cleanup(func() {
		if _, err := client.User.Delete(context.Background(), userUUID); err != nil {
			t.Logf("cleanup: failed to delete test user %q: %v", userUUID, err)
		}
	})
	return userUUID, graphUUID
}

func TestLiveCreateAndPersistUUIDResources(t *testing.T) {
	client := requireLiveClient(t)
	ctx := context.Background()
	userUUID, _ := createLiveUser(t, client)
	threadUUID, err := CreateThread(ctx, client, userUUID)
	if err != nil {
		t.Fatalf("CreateThread: %v", err)
	}
	t.Cleanup(func() {
		if _, err := client.Thread.Delete(context.Background(), threadUUID); err != nil {
			t.Logf("cleanup: failed to delete test thread %q: %v", threadUUID, err)
		}
	})

	before := NewBeforeModelCallback(client,
		WithUserUUID(userUUID), WithThreadUUID(threadUUID))
	after := NewAfterModelCallback(client, WithAfterThreadUUID(threadUUID))
	cc := newFakeCallbackContext(genai.NewContentFromText("My favorite language is Go.", genai.RoleUser))
	req := &model.LLMRequest{Contents: []*genai.Content{
		genai.NewContentFromText("My favorite language is Go.", genai.RoleUser),
	}}
	if _, err := before(cc, req); err != nil {
		t.Fatalf("before callback: %v", err)
	}
	if _, err := after(cc, &model.LLMResponse{
		Content: genai.NewContentFromText("I will remember that.", genai.RoleModel),
	}, nil); err != nil {
		t.Fatalf("after callback: %v", err)
	}
}

func TestLiveGraphSearchScopes(t *testing.T) {
	client := requireLiveClient(t)
	_, graphUUID := createLiveUser(t, client)
	for _, scope := range []SearchScope{
		SearchScopeEdges, SearchScopeNodes, SearchScopeEpisodes,
		SearchScopeObservations, SearchScopeThreadSummaries, SearchScopeAuto,
	} {
		t.Run(string(scope), func(t *testing.T) {
			_, err := searchGraph(context.Background(), client, graphUUID, graphSearchRequest{
				query: "favorite language", scope: scope,
			})
			if err != nil {
				t.Fatalf("search scope %q: %v", scope, err)
			}
		})
	}
}

func TestLiveMemoryServiceSearch(t *testing.T) {
	client := requireLiveClient(t)
	_, graphUUID := createLiveUser(t, client)
	svc := NewMemoryService(client, WithMemoryGraphUUID(graphUUID))
	resp, err := svc.SearchMemory(context.Background(), &memory.SearchRequest{
		Query: "favorite language",
	})
	if err != nil {
		t.Fatalf("SearchMemory: %v", err)
	}
	if resp == nil {
		t.Fatal("SearchMemory returned nil response")
	}
}
