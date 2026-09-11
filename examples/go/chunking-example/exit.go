package main

import (
	"errors"
	"fmt"
)

// ErrChunkProcessingFailed is returned when one or more chunks failed processing.
var ErrChunkProcessingFailed = errors.New("chunk processing failed")

// RequireEpisodeUUIDForWait returns an error when --wait is set but episode UUID is missing.
func RequireEpisodeUUIDForWait(wait bool, episodeUUID string) error {
	if !wait {
		return nil
	}
	if episodeUUID == "" {
		return fmt.Errorf("episode UUID is required when --wait is set")
	}
	return nil
}

// ChunkProcessingExitError returns a non-nil error when any chunks failed, so the
// process can exit nonzero after printing the summary.
func ChunkProcessingExitError(failed int) error {
	if failed <= 0 {
		return nil
	}
	return fmt.Errorf("%w: %d chunk(s) failed", ErrChunkProcessingFailed, failed)
}
