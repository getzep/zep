package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

type stubEpisode struct {
	processed bool
	taskID    string
	err       error
}

type stubTask struct {
	status string
	errMsg string
	err    error
}

type sequenceEpisodeClient struct {
	calls int
	seq   []stubEpisode
}

func (s *sequenceEpisodeClient) GetEpisode(_ context.Context, _ string) (processed bool, taskID string, err error) {
	if s.calls >= len(s.seq) {
		last := s.seq[len(s.seq)-1]
		return last.processed, last.taskID, last.err
	}
	ep := s.seq[s.calls]
	s.calls++
	return ep.processed, ep.taskID, ep.err
}

type fixedTaskClient struct {
	task stubTask
}

func (f fixedTaskClient) GetTask(_ context.Context, _ string) (status string, errMsg string, err error) {
	return f.task.status, f.task.errMsg, f.task.err
}

func TestWaitForEpisodeSucceedsWhenProcessed(t *testing.T) {
	eps := &sequenceEpisodeClient{seq: []stubEpisode{
		{processed: false},
		{processed: false},
		{processed: true},
	}}
	err := WaitForEpisode(context.Background(), "ep-1", WaitOptions{
		Timeout:      5 * time.Second,
		PollInterval: 0,
		Sleep:        func(time.Duration) {},
		GetEpisode:   eps.GetEpisode,
	})
	if err != nil {
		t.Fatalf("WaitForEpisode: %v", err)
	}
	if eps.calls != 3 {
		t.Fatalf("expected 3 polls, got %d", eps.calls)
	}
}

func TestWaitForEpisodeTimesOut(t *testing.T) {
	eps := &sequenceEpisodeClient{seq: []stubEpisode{
		{processed: false},
	}}
	err := WaitForEpisode(context.Background(), "ep-2", WaitOptions{
		Timeout:      5 * time.Millisecond,
		PollInterval: 0,
		Sleep:        func(time.Duration) {},
		GetEpisode:   eps.GetEpisode,
	})
	if err == nil {
		t.Fatal("expected timeout error")
	}
	if !errors.Is(err, ErrWaitTimeout) && !strings.Contains(strings.ToLower(err.Error()), "timed out") {
		t.Fatalf("expected timeout, got: %v", err)
	}
}

func TestWaitForEpisodeFailFastOnTaskStatuses(t *testing.T) {
	for _, status := range []string{"failed", "error", "canceled", "cancelled", "partial"} {
		t.Run(status, func(t *testing.T) {
			eps := &sequenceEpisodeClient{seq: []stubEpisode{
				{processed: false, taskID: "task-1"},
			}}
			tasks := fixedTaskClient{task: stubTask{status: status, errMsg: status + " boom"}}
			err := WaitForEpisode(context.Background(), "ep-x", WaitOptions{
				Timeout:      5 * time.Second,
				PollInterval: 0,
				Sleep:        func(time.Duration) {},
				GetEpisode:   eps.GetEpisode,
				GetTask:      tasks.GetTask,
			})
			if err == nil {
				t.Fatal("expected failure")
			}
			msg := strings.ToLower(err.Error())
			if !strings.Contains(msg, "fail") &&
				!strings.Contains(msg, "error") &&
				!strings.Contains(msg, "cancel") &&
				!strings.Contains(msg, "partial") {
				t.Fatalf("error should mention failure status, got: %v", err)
			}
		})
	}
}

func TestTaskFailureStatusesCanonical(t *testing.T) {
	required := []string{"failed", "error", "canceled", "cancelled", "partial"}
	for _, s := range required {
		if !IsTaskFailureStatus(s) {
			t.Fatalf("expected %q to be a failure status", s)
		}
	}
	for _, s := range []string{"succeeded", "completed", "complete", "success", "running"} {
		if IsTaskFailureStatus(s) {
			t.Fatalf("did not expect %q to be a failure status", s)
		}
	}
}
