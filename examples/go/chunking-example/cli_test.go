package main

import (
	"strings"
	"testing"
)

func TestParseArgsRequiresDocumentAndUserID(t *testing.T) {
	_, err := ParseArgs([]string{})
	if err == nil {
		t.Fatal("expected error when document and --user-id are missing")
	}
	if !strings.Contains(strings.ToLower(err.Error()), "user-id") &&
		!strings.Contains(strings.ToLower(err.Error()), "document") &&
		!strings.Contains(strings.ToLower(err.Error()), "required") {
		t.Fatalf("error should mention required args, got: %v", err)
	}

	_, err = ParseArgs([]string{"sample_document.txt"})
	if err == nil {
		t.Fatal("expected error when --user-id is missing")
	}
}

func TestParseArgsParsesDocumentFlags(t *testing.T) {
	opts, err := ParseArgs([]string{
		"sample_document.txt",
		"--user-id", "user123",
		"--chunk-size", "4000",
		"--chunk-overlap", "200",
		"--dry-run",
		"--wait",
	})
	if err != nil {
		t.Fatalf("ParseArgs: %v", err)
	}
	if opts.Document != "sample_document.txt" {
		t.Fatalf("Document=%q", opts.Document)
	}
	if opts.UserID != "user123" {
		t.Fatalf("UserID=%q", opts.UserID)
	}
	if opts.ChunkSize != 4000 {
		t.Fatalf("ChunkSize=%d", opts.ChunkSize)
	}
	if opts.ChunkOverlap != 200 {
		t.Fatalf("ChunkOverlap=%d", opts.ChunkOverlap)
	}
	if !opts.DryRun {
		t.Fatal("expected DryRun true")
	}
	if !opts.Wait {
		t.Fatal("expected Wait true")
	}
}

func TestParseArgsDefaults(t *testing.T) {
	opts, err := ParseArgs([]string{"doc.txt", "--user-id", "u1"})
	if err != nil {
		t.Fatalf("ParseArgs: %v", err)
	}
	if opts.ChunkSize != 6000 {
		t.Fatalf("default ChunkSize=%d, want 6000", opts.ChunkSize)
	}
	if opts.ChunkOverlap != 200 {
		t.Fatalf("default ChunkOverlap=%d, want 200", opts.ChunkOverlap)
	}
	if opts.DryRun {
		t.Fatal("DryRun should default false")
	}
	if opts.Wait {
		t.Fatal("Wait should default false")
	}
}

func TestParseArgsAcceptsFlagsBeforeDocument(t *testing.T) {
	opts, err := ParseArgs([]string{
		"--user-id", "u2",
		"--dry-run",
		"handbook.txt",
	})
	if err != nil {
		t.Fatalf("ParseArgs: %v", err)
	}
	if opts.Document != "handbook.txt" || opts.UserID != "u2" || !opts.DryRun {
		t.Fatalf("unexpected opts: %+v", opts)
	}
}
