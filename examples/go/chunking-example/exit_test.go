package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestWaitForEpisodeRequiresUUID(t *testing.T) {
	err := WaitForEpisode(context.Background(), "", WaitOptions{
		Timeout:      time.Second,
		PollInterval: 0,
		Sleep:        func(time.Duration) {},
		GetEpisode: func(context.Context, string) (bool, string, error) {
			t.Fatal("GetEpisode should not be called for empty UUID")
			return false, "", nil
		},
	})
	if err == nil {
		t.Fatal("expected error for empty episode UUID")
	}
	if !strings.Contains(strings.ToLower(err.Error()), "uuid") &&
		!strings.Contains(strings.ToLower(err.Error()), "required") {
		t.Fatalf("error should mention UUID requirement, got: %v", err)
	}
}

func TestRequireEpisodeUUIDForWait(t *testing.T) {
	if err := RequireEpisodeUUIDForWait(false, ""); err != nil {
		t.Fatalf("wait=false should allow empty UUID: %v", err)
	}
	if err := RequireEpisodeUUIDForWait(false, "ep-1"); err != nil {
		t.Fatalf("wait=false with UUID: %v", err)
	}
	if err := RequireEpisodeUUIDForWait(true, "ep-1"); err != nil {
		t.Fatalf("wait=true with UUID: %v", err)
	}
	err := RequireEpisodeUUIDForWait(true, "")
	if err == nil {
		t.Fatal("wait=true with empty UUID must fail")
	}
}

func TestChunkProcessingExitError(t *testing.T) {
	if err := ChunkProcessingExitError(0); err != nil {
		t.Fatalf("zero failures should be nil, got %v", err)
	}
	err := ChunkProcessingExitError(2)
	if err == nil {
		t.Fatal("expected error when chunks failed")
	}
	if !errors.Is(err, ErrChunkProcessingFailed) &&
		!strings.Contains(strings.ToLower(err.Error()), "fail") {
		t.Fatalf("expected chunk failure error, got: %v", err)
	}
}
