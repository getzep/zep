package zepadk

import (
	"context"
	"log/slog"

	zep "github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"
	zepoption "github.com/getzep/zep-go/v3/option"
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
// edge-mapping) be table-tested with an in-memory fake instead of requiring a
// live Zep account or HTTP mocking.
//
// The methods mirror the subset of the Zep SDK this package calls:
//
//   - AddMessages persists a thread turn (and can return the Context Block).
//   - GetUserContext fetches the Context Block for a thread without writing.
//   - Search runs a graph search.
//   - AddUser creates a Zep user (used by EnsureUser).
//   - CreateThread creates a Zep thread (used by EnsureThread).
type zepAPI interface {
	AddMessages(ctx context.Context, threadID string, req *zep.AddThreadMessagesRequest, opts ...zepoption.RequestOption) (*zep.AddThreadMessagesResponse, error)
	Search(ctx context.Context, req *zep.GraphSearchQuery, opts ...zepoption.RequestOption) (*zep.GraphSearchResults, error)
	AddUser(ctx context.Context, req *zep.CreateUserRequest, opts ...zepoption.RequestOption) (*zep.User, error)
	CreateThread(ctx context.Context, req *zep.CreateThreadRequest, opts ...zepoption.RequestOption) (*zep.Thread, error)
}

// clientAdapter adapts the concrete *zepclient.Client to the zepAPI seam by
// flattening the Thread / Graph sub-clients into top-level methods.
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

func (a *clientAdapter) AddMessages(ctx context.Context, threadID string, req *zep.AddThreadMessagesRequest, opts ...zepoption.RequestOption) (*zep.AddThreadMessagesResponse, error) {
	return a.client.Thread.AddMessages(ctx, threadID, req, opts...)
}

func (a *clientAdapter) Search(ctx context.Context, req *zep.GraphSearchQuery, opts ...zepoption.RequestOption) (*zep.GraphSearchResults, error) {
	return a.client.Graph.Search(ctx, req, opts...)
}

func (a *clientAdapter) AddUser(ctx context.Context, req *zep.CreateUserRequest, opts ...zepoption.RequestOption) (*zep.User, error) {
	return a.client.User.Add(ctx, req, opts...)
}

func (a *clientAdapter) CreateThread(ctx context.Context, req *zep.CreateThreadRequest, opts ...zepoption.RequestOption) (*zep.Thread, error) {
	return a.client.Thread.Create(ctx, req, opts...)
}

// truncateMessageContent returns content trimmed to Zep's per-message character
// limit, never silently dropping a too-long message. When truncation happens it
// logs a warning containing only lengths (never the content itself or any other
// PII) so the caller can observe that a turn was clipped.
//
// The original (untruncated) content is returned unchanged when it already fits.
func truncateMessageContent(logger *slog.Logger, threadID, content string) string {
	if len(content) <= maxMessageContentChars {
		return content
	}
	if logger == nil {
		logger = slog.Default()
	}
	// Truncate on a rune boundary so we never emit invalid UTF-8.
	truncated := truncateRunes(content, messageTruncateChars)
	logger.Warn("zepadk: message content exceeds Zep limit; truncating before persist",
		slog.String("thread_id", threadID),
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
