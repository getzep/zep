package server

import (
	"testing"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/config"
)

func TestNewRegistersTools(t *testing.T) {
	cfg := &config.Config{
		ZepAPIKey:     "test-key",
		LogLevel:      "info",
		ServerName:    "zep-mcp-server",
		ServerVersion: "0.1.0",
	}

	srv, err := New(cfg)
	if err != nil {
		t.Fatalf("New() returned error: %v", err)
	}
	if srv == nil {
		t.Fatal("New() returned nil server")
	}
}
