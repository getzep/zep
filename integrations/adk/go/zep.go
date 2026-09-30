package zepadk

import (
	"context"
	"errors"
	"log/slog"

	zep "github.com/getzep/zep-go/v4"
	zepclient "github.com/getzep/zep-go/v4/client"
	zepcore "github.com/getzep/zep-go/v4/core"
)

// maxMessageContentChars is Zep's hard limit on the content of a single thread
// message. Exceeding it makes Thread.AddMessages return HTTP 400, so this
// package truncates message content to messageTruncateChars (slightly under the
// limit) before persisting. See https://help.getzep.com/adding-messages.
const maxMessageContentChars = 4096

// messageTruncateChars is the length this package truncates message content to
// before persisting. It sits a little under maxMessageContentChars to leave
// headroom and make truncation observable in tests.
const messageTruncateChars = 4000

// zepAPI is the minimal seam over the concrete *zepclient.Client used by this
// package. Introducing it lets the success paths (persist / inject / dedup /
// result mapping) be table-tested with an in-memory fake instead of requiring
// a live Zep account or HTTP mocking.
//
// The methods mirror the subset of the Zep v4 SDK this package calls. Zep v4
// addresses every thread and graph by a server-generated UUID, and it splits
// the single v3 graph search method into one method for each scope, so the
// seam carries one method for each supported scope.
type zepAPI interface {
	AddMessages(ctx context.Context, threadUUID string, req *zep.AddMessagesRequest) (*zep.AddMessagesResult, error)
	SearchEdges(ctx context.Context, graphUUID string, req *zep.GraphSearchEdgesRequest) ([]*zep.Edge, error)
	SearchNodes(ctx context.Context, graphUUID string, req *zep.GraphSearchNodesRequest) ([]*zep.Node, error)
	SearchEpisodes(ctx context.Context, graphUUID string, req *zep.GraphSearchEpisodesRequest) ([]*zep.Episode, error)
	SearchObservations(ctx context.Context, graphUUID string, req *zep.GraphSearchObservationsRequest) ([]*zep.Observation, error)
	SearchThreadSummaries(ctx context.Context, graphUUID string, req *zep.GraphSearchThreadSummariesRequest) ([]*zep.ThreadSummary, error)
	GetGraphContext(ctx context.Context, graphUUID string, req *zep.GraphContextRequest) (*zep.GraphContextResponse, error)
	CreateUser(ctx context.Context, req *zep.CreateUserRequest) (*zep.User, error)
	CreateThread(ctx context.Context, req *zep.CreateThreadRequest) (*zep.Thread, error)
}

// clientAdapter adapts the concrete *zepclient.Client to the zepAPI seam by
// flattening the Thread / Graph / User sub-clients into top-level methods.
// Zep v4 returns a cursor page for each search, so each search method follows
// the pages until it collects the requested number of results.
type clientAdapter struct {
	client *zepclient.Client
}

// newZepAPI wraps a concrete client in the zepAPI seam, returning nil when the
// client is nil so callers can keep their existing nil-means-no-op checks.
func newZepAPI(client *zepclient.Client) zepAPI {
	if client == nil {
		return nil
	}
	return &clientAdapter{client: client}
}

func (a *clientAdapter) AddMessages(ctx context.Context, threadUUID string, req *zep.AddMessagesRequest) (*zep.AddMessagesResult, error) {
	return a.client.Thread.AddMessages(ctx, threadUUID, req)
}

func (a *clientAdapter) SearchEdges(ctx context.Context, graphUUID string, req *zep.GraphSearchEdgesRequest) ([]*zep.Edge, error) {
	page, err := a.client.Graph.SearchEdges(ctx, graphUUID, req)
	if err != nil {
		return nil, err
	}
	return collectPage(ctx, page, req.Limit)
}

func (a *clientAdapter) SearchNodes(ctx context.Context, graphUUID string, req *zep.GraphSearchNodesRequest) ([]*zep.Node, error) {
	page, err := a.client.Graph.SearchNodes(ctx, graphUUID, req)
	if err != nil {
		return nil, err
	}
	return collectPage(ctx, page, req.Limit)
}

func (a *clientAdapter) SearchEpisodes(ctx context.Context, graphUUID string, req *zep.GraphSearchEpisodesRequest) ([]*zep.Episode, error) {
	page, err := a.client.Graph.SearchEpisodes(ctx, graphUUID, req)
	if err != nil {
		return nil, err
	}
	return collectPage(ctx, page, req.Limit)
}

func (a *clientAdapter) SearchObservations(ctx context.Context, graphUUID string, req *zep.GraphSearchObservationsRequest) ([]*zep.Observation, error) {
	page, err := a.client.Graph.SearchObservations(ctx, graphUUID, req)
	if err != nil {
		return nil, err
	}
	return collectPage(ctx, page, req.Limit)
}

func (a *clientAdapter) SearchThreadSummaries(ctx context.Context, graphUUID string, req *zep.GraphSearchThreadSummariesRequest) ([]*zep.ThreadSummary, error) {
	page, err := a.client.Graph.SearchThreadSummaries(ctx, graphUUID, req)
	if err != nil {
		return nil, err
	}
	return collectPage(ctx, page, req.Limit)
}

// collectPage reads results from a Zep v4 cursor page, and it follows the
// pages until it collects limit results or no page remains. A nil limit
// collects every result.
func collectPage[C comparable, T any, R any](ctx context.Context, page *zepcore.Page[C, T, R], limit *int) ([]T, error) {
	var out []T
	iter := page.Iterator()
	for iter.Next(ctx) {
		if limit != nil && len(out) >= *limit {
			break
		}
		out = append(out, iter.Current())
	}
	if err := iter.Err(); err != nil && !errors.Is(err, zepcore.ErrNoPages) {
		return nil, err
	}
	return out, nil
}

func (a *clientAdapter) GetGraphContext(ctx context.Context, graphUUID string, req *zep.GraphContextRequest) (*zep.GraphContextResponse, error) {
	return a.client.Graph.GetContext(ctx, graphUUID, req)
}

func (a *clientAdapter) CreateUser(ctx context.Context, req *zep.CreateUserRequest) (*zep.User, error) {
	return a.client.User.Create(ctx, req)
}

func (a *clientAdapter) CreateThread(ctx context.Context, req *zep.CreateThreadRequest) (*zep.Thread, error) {
	return a.client.Thread.Create(ctx, req)
}

// truncateMessageContent returns content trimmed to Zep's per-message character
// limit, never silently dropping a too-long message. When truncation happens it
// logs a warning containing only lengths (never the content itself or any other
// PII) so the caller can observe that a turn was clipped.
//
// The original (untruncated) content is returned unchanged when it already fits.
func truncateMessageContent(logger *slog.Logger, threadUUID, content string) string {
	if len(content) <= maxMessageContentChars {
		return content
	}
	if logger == nil {
		logger = slog.Default()
	}
	// Truncate on a rune boundary so we never emit invalid UTF-8.
	truncated := truncateRunes(content, messageTruncateChars)
	logger.Warn("zepadk: message content exceeds Zep limit; truncating before persist",
		slog.String("thread_uuid", threadUUID),
		slog.Int("original_chars", len(content)),
		slog.Int("truncated_chars", len(truncated)),
		slog.Int("limit_chars", maxMessageContentChars))
	return truncated
}

// truncateRunes returns s clipped to at most maxBytes bytes without splitting a
// multi-byte rune. Because messageTruncateChars is below maxMessageContentChars,
// the byte-bounded result always satisfies Zep's character limit.
func truncateRunes(s string, maxBytes int) string {
	if len(s) <= maxBytes {
		return s
	}
	// Walk back from maxBytes to the start of the rune that straddles the cut.
	cut := maxBytes
	for cut > 0 && !utf8RuneStart(s[cut]) {
		cut--
	}
	return s[:cut]
}

// utf8RuneStart reports whether b is the first byte of a UTF-8 rune (i.e. not a
// 0b10xxxxxx continuation byte).
func utf8RuneStart(b byte) bool {
	return b&0xC0 != 0x80
}

// deref returns the value behind s, or "" when s is nil. Zep v4 models most
// string fields as pointers.
func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}
