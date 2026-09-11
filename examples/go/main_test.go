package main

import (
	"errors"
	"os"
	"strings"
	"testing"
)

func TestParseRootCommandDefault(t *testing.T) {
	cmd, err := ParseRootCommand(nil)
	if err != nil {
		t.Fatalf("ParseRootCommand: %v", err)
	}
	if cmd != RootCommandUserGraph {
		t.Fatalf("cmd=%q, want %q", cmd, RootCommandUserGraph)
	}

	cmd, err = ParseRootCommand([]string{})
	if err != nil {
		t.Fatalf("ParseRootCommand: %v", err)
	}
	if cmd != RootCommandUserGraph {
		t.Fatalf("cmd=%q, want %q", cmd, RootCommandUserGraph)
	}
}

func TestParseRootCommandEntityTypes(t *testing.T) {
	cmd, err := ParseRootCommand([]string{"entity-types"})
	if err != nil {
		t.Fatalf("ParseRootCommand: %v", err)
	}
	if cmd != RootCommandEntityTypes {
		t.Fatalf("cmd=%q, want %q", cmd, RootCommandEntityTypes)
	}
}

func TestParseRootCommandUnknownShowsUsageError(t *testing.T) {
	_, err := ParseRootCommand([]string{"wat"})
	if err == nil {
		t.Fatal("expected error for unknown command")
	}
	if !errors.Is(err, ErrRootUsage) && !strings.Contains(strings.ToLower(err.Error()), "usage") {
		t.Fatalf("error should be a usage error, got: %v", err)
	}
	msg := err.Error()
	if !strings.Contains(msg, "entity-types") || !strings.Contains(msg, "go run") {
		t.Fatalf("usage error should include help text, got: %v", err)
	}
}

func TestModulePathFinalSegmentSafeForInstall(t *testing.T) {
	path := modulePath()
	parts := strings.Split(path, "/")
	final := parts[len(parts)-1]
	if final == "go" || final == "" {
		t.Fatalf("module path %q ends with %q; go install would emit a binary named go", path, final)
	}
	if final != "zep-go-examples" {
		t.Fatalf("module path final segment=%q, want zep-go-examples", final)
	}
}

func TestExampleRunnersReturnError(t *testing.T) {
	userGraph, err := os.ReadFile("user_graph.go")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(userGraph), "func runUserGraph() error") {
		t.Fatal("runUserGraph must return error so live failures exit non-zero")
	}
	entityTypes, err := os.ReadFile("entity_types.go")
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(entityTypes), "func runEntityTypes() error") {
		t.Fatal("runEntityTypes must return error so live failures exit non-zero")
	}
}
