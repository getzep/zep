package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"
)

// ErrWaitTimeout is returned when episode processing does not complete in time.
var ErrWaitTimeout = errors.New("timed out waiting for episode processing")

var taskFailureStatuses = map[string]struct{}{
	"failed":    {},
	"error":     {},
	"canceled":  {},
	"cancelled": {},
	"partial":   {},
}

// IsTaskFailureStatus reports whether status is a terminal unsuccessful task status.
func IsTaskFailureStatus(status string) bool {
	_, ok := taskFailureStatuses[strings.ToLower(strings.TrimSpace(status))]
	return ok
}

// WaitOptions configures bounded episode processing polling.
type WaitOptions struct {
	Timeout      time.Duration
	PollInterval time.Duration
	Sleep        func(time.Duration)
	GetEpisode   func(ctx context.Context, episodeUUID string) (processed bool, taskID string, err error)
	GetTask      func(ctx context.Context, taskID string) (status string, errMsg string, err error)
}

// WaitForEpisode polls until episode.processed is true, a linked task fails, or timeout.
func WaitForEpisode(ctx context.Context, episodeUUID string, opts WaitOptions) error {
	if episodeUUID == "" {
		return fmt.Errorf("episode UUID is required")
	}
	if opts.GetEpisode == nil {
		return fmt.Errorf("GetEpisode is required")
	}
	if opts.Timeout <= 0 {
		opts.Timeout = 180 * time.Second
	}
	if opts.PollInterval < 0 {
		opts.PollInterval = 0
	}
	if opts.PollInterval == 0 && opts.Timeout >= time.Second {
		opts.PollInterval = 2 * time.Second
	}
	if opts.Sleep == nil {
		opts.Sleep = time.Sleep
	}

	deadline := time.Now().Add(opts.Timeout)
	for {
		if err := ctx.Err(); err != nil {
			return err
		}

		processed, taskID, err := opts.GetEpisode(ctx, episodeUUID)
		if err != nil {
			return fmt.Errorf("get episode %s: %w", episodeUUID, err)
		}
		if processed {
			return nil
		}

		if taskID != "" && opts.GetTask != nil {
			status, errMsg, err := opts.GetTask(ctx, taskID)
			if err != nil {
				return fmt.Errorf("get task %s: %w", taskID, err)
			}
			if IsTaskFailureStatus(status) {
				return fmt.Errorf("episode %s task %s ended with status=%s: %s", episodeUUID, taskID, status, errMsg)
			}
		}

		if time.Now().After(deadline) {
			return fmt.Errorf("%w for episode %s after %s", ErrWaitTimeout, episodeUUID, opts.Timeout)
		}
		opts.Sleep(opts.PollInterval)
	}
}
