// Package zepadk integrates [Zep] long-term agent memory with the
// [Google Agent Development Kit (ADK) for Go].
//
// It provides four composable building blocks:
//
//   - [NewBeforeModelCallback] returns an [llmagent.BeforeModelCallback] that,
//     on a genuinely new user turn, persists the user's latest message to a Zep
//     thread and injects the resulting Zep Context Block into the request's
//     system instruction. It skips re-persisting and re-injecting on tool-loop
//     continuations (turns whose latest content is a function response). Attach
//     it via [llmagent.Config.BeforeModelCallbacks].
//   - [NewAfterModelCallback] returns an [llmagent.AfterModelCallback] that
//     persists the assistant's text reply to the same Zep thread so the user
//     graph sees both halves of the conversation. Attach it via
//     [llmagent.Config.AfterModelCallbacks].
//   - [NewMemoryService] returns an ADK [memory.Service] backed by Zep's
//     user-graph search. Attach it at the runner via
//     [runner.Config.MemoryService]; ADK tools reach it through
//     ToolContext.SearchMemory.
//   - [NewGraphSearchTool] returns a [tool.Tool] the model can call to search
//     the user's Zep knowledge graph on demand.
//
// All Zep calls are guarded so that a Zep failure never crashes the host
// agent: when the client is nil (for example because ZEP_API_KEY is unset) the
// callback and tools degrade to no-ops, and transient Zep errors are logged
// and swallowed rather than surfaced to the model.
//
// Zep ingestion is asynchronous: a message added during a turn is not
// guaranteed to be retrievable within that same turn. The Context Block
// returned by Thread.AddMessages reflects prior turns. Design for eventual
// availability.
//
// [Zep]: https://www.getzep.com
// [Google Agent Development Kit (ADK) for Go]: https://github.com/google/adk-go
package zepadk

import (
	"context"
	"log/slog"
	"strings"

	zep "github.com/getzep/zep-go/v3"
	zepclient "github.com/getzep/zep-go/v3/client"

	"google.golang.org/adk/agent"
	"google.golang.org/adk/agent/llmagent"
	"google.golang.org/adk/model"
	"google.golang.org/genai"
)

// DefaultContextPrefix is prepended to the Zep Context Block when it is
// injected into the model's system instruction.
const DefaultContextPrefix = "Relevant memory retrieved from Zep about the user:\n"

// callbackOptions holds the resolved configuration for a BeforeModelCallback.
type callbackOptions struct {
	contextPrefix string
	userName      string
	logger        *slog.Logger
}

// CallbackOption customizes the behavior of [NewBeforeModelCallback].
type CallbackOption func(*callbackOptions)

// WithContextPrefix overrides the text prepended to the Zep Context Block
// before it is injected into the system instruction. Pass an empty string to
// inject the Context Block verbatim.
func WithContextPrefix(prefix string) CallbackOption {
	return func(o *callbackOptions) { o.contextPrefix = prefix }
}

// WithUserMessageName sets the optional display name attached to the user
// message persisted to Zep. Supplying a real name helps Zep resolve the
// user's identity in the graph.
func WithUserMessageName(name string) CallbackOption {
	return func(o *callbackOptions) { o.userName = name }
}

// WithLogger sets the [slog.Logger] used to report Zep errors. Defaults to
// [slog.Default].
func WithLogger(logger *slog.Logger) CallbackOption {
	return func(o *callbackOptions) {
		if logger != nil {
			o.logger = logger
		}
	}
}

func resolveCallbackOptions(opts []CallbackOption) callbackOptions {
	resolved := callbackOptions{
		contextPrefix: DefaultContextPrefix,
		logger:        slog.Default(),
	}
	for _, opt := range opts {
		opt(&resolved)
	}
	return resolved
}

// NewBeforeModelCallback returns an ADK [llmagent.BeforeModelCallback] that
// gives an agent persistent, cross-session memory backed by Zep.
//
// On a genuinely new user turn the callback:
//
//  1. Extracts the user's latest message from the callback context.
//  2. Truncates it to Zep's per-message limit if needed (logging a
//     lengths-only warning; content is never dropped or logged).
//  3. Persists it to the Zep thread whose ID equals the ADK session ID,
//     requesting the Context Block in the same round-trip
//     (Thread.AddMessages with ReturnContext=true).
//  4. Injects the returned Context Block into req.Config.SystemInstruction.
//
// During a tool loop the same model turn fires repeatedly: ADK calls the
// before-model callback again after each tool result so the model can continue.
// On those continuations the latest content in req.Contents is a function
// response, not new user input. The callback detects this (see
// [IsToolLoopContinuation]) and returns early without re-persisting the user
// message or re-injecting the Context Block, so a turn that calls search_memory
// is recorded in Zep exactly once.
//
// It returns (nil, nil) so ADK proceeds to the real model with the mutated
// request. A nil client (for example when ZEP_API_KEY is unset) makes the
// callback a no-op. Zep failures are logged via the configured logger and
// never propagated, so the agent continues without memory rather than
// crashing.
//
// The integration contract is: ADK session ID maps to the Zep thread ID and
// ADK user ID maps to the Zep user ID. Create the Zep user and thread out of
// band before the first turn (see [EnsureUser] and [EnsureThread]).
func NewBeforeModelCallback(client *zepclient.Client, opts ...CallbackOption) llmagent.BeforeModelCallback {
	return newBeforeModelCallback(newZepAPI(client), opts...)
}

// newBeforeModelCallback is the seam-friendly core of [NewBeforeModelCallback].
// A nil api makes the callback a no-op.
func newBeforeModelCallback(api zepAPI, opts ...CallbackOption) llmagent.BeforeModelCallback {
	cfg := resolveCallbackOptions(opts)

	return func(cc agent.CallbackContext, req *model.LLMRequest) (*model.LLMResponse, error) {
		// No client configured: do not short-circuit the model.
		if api == nil {
			return nil, nil
		}

		// Tool-loop continuation: the model is resuming after a tool result,
		// not starting a new user turn. Persisting again would duplicate the
		// user message in the graph and re-inject the Context Block, so skip.
		if req != nil && IsToolLoopContinuation(req.Contents) {
			return nil, nil
		}

		threadID := cc.SessionID()
		latest := LastUserText(cc.UserContent())
		if threadID == "" || latest == "" {
			return nil, nil
		}

		message := &zep.Message{
			Role:    zep.RoleTypeUserRole,
			Content: truncateMessageContent(cfg.logger, threadID, latest),
		}
		if cfg.userName != "" {
			message.Name = zep.String(cfg.userName)
		}

		resp, err := api.AddMessages(cc, threadID, &zep.AddThreadMessagesRequest{
			ReturnContext: zep.Bool(true),
			Messages:      []*zep.Message{message},
		})
		if err != nil {
			// Never crash the host agent on a Zep failure: log and proceed
			// without injecting memory for this turn.
			cfg.logger.Error("zepadk: persisting user message failed; proceeding without memory",
				slog.String("thread_id", threadID), slog.Any("error", err))
			return nil, nil
		}

		if resp != nil && resp.Context != nil && *resp.Context != "" {
			InjectSystemInstruction(req, cfg.contextPrefix+*resp.Context)
		}
		return nil, nil
	}
}

// AfterCallbackOption customizes the behavior of [NewAfterModelCallback].
type AfterCallbackOption func(*afterCallbackOptions)

// afterCallbackOptions holds the resolved configuration for an
// AfterModelCallback.
type afterCallbackOptions struct {
	assistantName string
	logger        *slog.Logger
}

// WithAssistantMessageName sets the optional display name attached to the
// assistant message persisted to Zep (for example the agent's name).
func WithAssistantMessageName(name string) AfterCallbackOption {
	return func(o *afterCallbackOptions) { o.assistantName = name }
}

// WithAfterLogger sets the [slog.Logger] used by the after-model callback to
// report Zep errors. Defaults to [slog.Default].
func WithAfterLogger(logger *slog.Logger) AfterCallbackOption {
	return func(o *afterCallbackOptions) {
		if logger != nil {
			o.logger = logger
		}
	}
}

func resolveAfterCallbackOptions(opts []AfterCallbackOption) afterCallbackOptions {
	resolved := afterCallbackOptions{logger: slog.Default()}
	for _, opt := range opts {
		opt(&resolved)
	}
	return resolved
}

// NewAfterModelCallback returns an ADK [llmagent.AfterModelCallback] that
// persists the assistant's text reply to the same Zep thread the user message
// was written to. Without it the user graph only ever sees the user half of
// the conversation.
//
// The callback:
//
//  1. Skips when the model returned an error, an empty/partial response, or a
//     response carrying only a function call (a tool-loop step, not a reply to
//     the user).
//  2. Truncates the reply to Zep's per-message limit if needed (logging a
//     lengths-only warning; content is never dropped or logged).
//  3. Persists it as an assistant message via Thread.AddMessages.
//
// It returns (nil, nil) so ADK keeps the model's real response. A nil client
// makes the callback a no-op, and Zep failures are logged and swallowed so a
// persistence failure never alters the response shown to the user.
func NewAfterModelCallback(client *zepclient.Client, opts ...AfterCallbackOption) llmagent.AfterModelCallback {
	return newAfterModelCallback(newZepAPI(client), opts...)
}

// newAfterModelCallback is the seam-friendly core of [NewAfterModelCallback].
// A nil api makes the callback a no-op.
func newAfterModelCallback(api zepAPI, opts ...AfterCallbackOption) llmagent.AfterModelCallback {
	cfg := resolveAfterCallbackOptions(opts)

	return func(cc agent.CallbackContext, resp *model.LLMResponse, respErr error) (*model.LLMResponse, error) {
		// Never touch the response; we only observe it.
		if api == nil || respErr != nil || resp == nil {
			return nil, nil
		}
		// Streaming emits partial chunks then a final consolidated response;
		// persist only the final one to avoid duplicating fragments.
		if resp.Partial {
			return nil, nil
		}

		threadID := cc.SessionID()
		reply := AssistantText(resp.Content)
		if threadID == "" || reply == "" {
			return nil, nil
		}

		message := &zep.Message{
			Role:    zep.RoleTypeAssistantRole,
			Content: truncateMessageContent(cfg.logger, threadID, reply),
		}
		if cfg.assistantName != "" {
			message.Name = zep.String(cfg.assistantName)
		}

		if _, err := api.AddMessages(cc, threadID, &zep.AddThreadMessagesRequest{
			Messages: []*zep.Message{message},
		}); err != nil {
			cfg.logger.Error("zepadk: persisting assistant message failed; reply unaffected",
				slog.String("thread_id", threadID), slog.Any("error", err))
		}
		return nil, nil
	}
}

// InjectSystemInstruction appends text to req.Config.SystemInstruction
// (a [*genai.Content]), allocating the config and instruction as needed. It is
// exported so callers building their own callbacks can reuse the injection
// logic.
func InjectSystemInstruction(req *model.LLMRequest, text string) {
	if req == nil || text == "" {
		return
	}
	if req.Config == nil {
		req.Config = &genai.GenerateContentConfig{}
	}
	if req.Config.SystemInstruction == nil {
		req.Config.SystemInstruction = genai.NewContentFromText(text, genai.RoleUser)
		return
	}
	req.Config.SystemInstruction.Parts = append(
		req.Config.SystemInstruction.Parts, genai.NewPartFromText(text))
}

// LastUserText returns the text of the last text part in c, or "" when c is
// nil or carries no text (for example a tool-result-only turn).
func LastUserText(c *genai.Content) string {
	if c == nil {
		return ""
	}
	for i := len(c.Parts) - 1; i >= 0; i-- {
		if c.Parts[i] != nil && c.Parts[i].Text != "" {
			return c.Parts[i].Text
		}
	}
	return ""
}

// AssistantText concatenates the text parts of an assistant response, joining
// multiple text parts with a single space. It returns "" when c is nil or
// carries no text — for example a response whose only part is a function call
// (a tool-loop step rather than a reply to the user).
func AssistantText(c *genai.Content) string {
	if c == nil {
		return ""
	}
	var b strings.Builder
	for _, p := range c.Parts {
		if p == nil || p.Text == "" {
			continue
		}
		if b.Len() > 0 {
			b.WriteByte(' ')
		}
		b.WriteString(p.Text)
	}
	return b.String()
}

// IsToolLoopContinuation reports whether contents represents a tool-loop
// continuation rather than a new user turn. During a tool loop ADK re-invokes
// the before-model callback after each tool result; on those passes the most
// recent content carries a function response (the tool's output) instead of
// fresh user text. Persisting the user message again on such a pass would
// duplicate it in the graph, so callers skip when this returns true.
//
// It returns false for the initial turn (whose latest content is user input)
// and for empty histories.
func IsToolLoopContinuation(contents []*genai.Content) bool {
	last := lastNonEmptyContent(contents)
	if last == nil {
		return false
	}
	return contentHasFunctionResponse(last)
}

// lastNonEmptyContent returns the final content in contents that has at least
// one non-nil part, or nil when there is none.
func lastNonEmptyContent(contents []*genai.Content) *genai.Content {
	for i := len(contents) - 1; i >= 0; i-- {
		c := contents[i]
		if c == nil {
			continue
		}
		for _, p := range c.Parts {
			if p != nil {
				return c
			}
		}
	}
	return nil
}

// contentHasFunctionResponse reports whether any part of c is a function
// response (a tool result fed back to the model).
func contentHasFunctionResponse(c *genai.Content) bool {
	for _, p := range c.Parts {
		if p != nil && p.FunctionResponse != nil {
			return true
		}
	}
	return false
}

// EnsureUser creates a Zep user, treating an "already exists" error as
// success so it is safe to call on every session start. Passing a real first
// name, last name, and email helps Zep resolve the user's identity in the
// graph. It is a no-op when client is nil.
func EnsureUser(ctx context.Context, client *zepclient.Client, userID, firstName, lastName, email string) error {
	if client == nil || userID == "" {
		return nil
	}
	req := &zep.CreateUserRequest{UserID: userID}
	if firstName != "" {
		req.FirstName = zep.String(firstName)
	}
	if lastName != "" {
		req.LastName = zep.String(lastName)
	}
	if email != "" {
		req.Email = zep.String(email)
	}
	if _, err := client.User.Add(ctx, req); err != nil {
		if isAlreadyExists(err) {
			return nil
		}
		return err
	}
	return nil
}

// EnsureThread creates a Zep thread for the given user, treating an "already
// exists" error as success so it is safe to call on every session start. The
// thread ID should equal the ADK session ID. It is a no-op when client is nil.
func EnsureThread(ctx context.Context, client *zepclient.Client, threadID, userID string) error {
	if client == nil || threadID == "" || userID == "" {
		return nil
	}
	if _, err := client.Thread.Create(ctx, &zep.CreateThreadRequest{
		ThreadID: threadID,
		UserID:   userID,
	}); err != nil {
		if isAlreadyExists(err) {
			return nil
		}
		return err
	}
	return nil
}
