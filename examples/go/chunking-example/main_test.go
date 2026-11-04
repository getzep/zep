package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/openai/openai-go/v3"
	openaioption "github.com/openai/openai-go/v3/option"
)

func TestContextualizeChunkUsesLowReasoning(t *testing.T) {
	var path string
	var body map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		raw, _ := io.ReadAll(r.Body)
		if err := json.Unmarshal(raw, &body); err != nil {
			t.Errorf("decode request: %v", err)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"id":"c","object":"chat.completion","created":0,"model":"gpt-6-luna","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"context"}}]}`)
	}))
	defer server.Close()

	client := openai.NewClient(openaioption.WithBaseURL(server.URL), openaioption.WithAPIKey("test"))
	result, err := contextualizeChunk(context.Background(), &client, "document", "chunk")
	if err != nil {
		t.Fatalf("contextualizeChunk: %v", err)
	}
	if path != "/chat/completions" {
		t.Errorf("path = %q, want /chat/completions", path)
	}
	if body["model"] != "gpt-6-luna" {
		t.Errorf("model = %v, want gpt-6-luna", body["model"])
	}
	if body["reasoning_effort"] != "low" {
		t.Errorf("reasoning_effort = %v, want low", body["reasoning_effort"])
	}
	if result == "" {
		t.Error("result is empty")
	}
}
