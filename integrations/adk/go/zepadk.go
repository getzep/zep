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
	"fmt"
	"log/slog"
	"strings"
	"sync"

	zep "github.com/getzep/zep-go/v4"
	zepclient "github.com/getzep/zep-go/v4/client"

	"google.golang.org/adk/agent"
	"google.golang.org/adk/agent/llmagent"
	"google.golang.org/adk/model"
	"google.golang.org/genai"
)

// DefaultContextPrefix is prepended to the Zep Context Block when it is
// injected into the model's system instruction.
//
// Deprecated: use [WithContextTemplate] and [DefaultContextTemplate] instead.
// DefaultContextPrefix remains exported only so callers using
// [WithContextPrefix] continue to compile.
const DefaultContextPrefix = "Relevant memory retrieved from Zep about the user:\n"

// DefaultContextTemplate is the default wrapper for the Zep context block
// (whether retrieved via the default single round-trip or produced by a
// custom [ContextBuilder]) before it is injected into the model's system
// instruction. It is rendered via [strings.ReplaceAll], substituting every
// occurrence of the literal "{context}" placeholder with the context text —
// never via fmt verbs — so context content containing "%", "{", or "}" is
// always safe to inject.
//
// This exact string is canonical across zep-adk's Python, Go, and TypeScript
// implementations — keep them in sync.
const DefaultContextTemplate = "The following context is retrieved from Zep, the agent's long-term memory. " +
	"It contains relevant facts, entities, and prior knowledge about the user. " +
	"Use it to inform your responses.\n\n" +
	"<ZEP_CONTEXT>\n" +
	"{context}\n" +
	"</ZEP_CONTEXT>"

// ContextInput is handed to a custom [ContextBuilder].
//
// Bundling the builder's inputs into a single struct (rather than positional
// arguments) lets us add fields later without breaking existing builders.
type ContextInput struct {
	// Client is the concrete Zep client in use by the callback (the same
	// value passed to [NewBeforeModelCallback]).
	Client     *zepclient.Client
	UserUUID   string
	ThreadUUID string
	// UserMessage is the user's message text for this turn (after
	// truncation to Zep's per-message limit).
	UserMessage string
	// Callback is the ADK callback context for this turn: session state,
	// invocation metadata.
	Callback agent.CallbackContext
	// Request is the outgoing model request about to be sent to the LLM.
	Request *model.LLMRequest
}

// ContextBuilder builds a custom context block to inject into the LLM prompt,
// instead of using Thread.AddMessages' ReturnContext. Returning "" (with a
// nil error) skips injection for that turn.
//
// Error semantics: if the builder returns a non-nil error, [NewBeforeModelCallback]
// logs a warning and skips injection for that turn — it never crashes the
// host agent and never prevents message persistence from completing. See
// [WithContextBuilder] for the full error-isolation contract between
// persistence and the builder.
type ContextBuilder func(ctx context.Context, in ContextInput) (string, error)

// callbackOptions holds the resolved configuration for a BeforeModelCallback.
type callbackOptions struct {
	contextTemplate string
	contextBuilder  ContextBuilder
	userName        string
	userUUID        string
	threadUUID      string
	userResolver    CallbackUUIDResolver
	threadResolver  CallbackUUIDResolver
	ignoreRoles     []string
	logger          *slog.Logger
}

type CallbackUUIDResolver func(agent.CallbackContext) string

// CallbackOption customizes the behavior of [NewBeforeModelCallback].
type CallbackOption func(*callbackOptions)

// WithContextPrefix overrides the text prepended to the Zep Context Block
// before it is injected into the system instruction.
//
// Deprecated: use [WithContextTemplate]. WithContextPrefix is implemented as
// a shim over WithContextTemplate: it sets the template to
// prefix+"{context}", so the injected text is identical to the pre-template
// behavior (no <ZEP_CONTEXT> wrapper).
func WithContextPrefix(prefix string) CallbackOption {
	return func(o *callbackOptions) { o.contextTemplate = prefix + "{context}" }
}

// WithContextTemplate overrides the template used to wrap the Zep context
// block before it is injected into the system instruction. template must
// contain a literal "{context}" placeholder; every occurrence is replaced
// with the context text via [strings.ReplaceAll] (never fmt verbs), so
// context text containing "%", "{", or "}" is always safe. Defaults to
// [DefaultContextTemplate].
func WithContextTemplate(template string) CallbackOption {
	return func(o *callbackOptions) { o.contextTemplate = template }
}

// WithContextBuilder configures a custom [ContextBuilder] that constructs the
// context block to inject, instead of relying on
// Thread.AddMessages(ReturnContext=true).
//
// When set, message persistence (Thread.AddMessages, without ReturnContext)
// and the builder run concurrently for lower latency. When unset (the
// default), the callback uses a single Thread.AddMessages(ReturnContext=true)
// round-trip.
//
// Error isolation (mandatory): persistence and the builder are isolated from
// each other's failure — one side failing never blocks or masks the other's
// result:
//
//   - If the builder returns an error, a warning is logged and injection is
//     skipped for this turn — but persistence still completes independently.
//   - If persistence fails, a warning is logged and the turn is not persisted
//     to Zep this pass — but a successful builder result may still be
//     injected into the prompt.
//
// (Go's tool-loop dedup, unlike Python/TS's persist-success-gated dedup, is
// structural: see [IsToolLoopContinuation]. It does not depend on whether
// persistence or the builder succeeded on a prior pass.)
func WithContextBuilder(b ContextBuilder) CallbackOption {
	return func(o *callbackOptions) { o.contextBuilder = b }
}

// WithUserMessageName sets the optional display name attached to the user
// message persisted to Zep. Supplying a real name helps Zep resolve the
// user's identity in the graph.
func WithUserMessageName(name string) CallbackOption {
	return func(o *callbackOptions) { o.userName = name }
}

func WithUserUUID(uuid string) CallbackOption {
	return func(o *callbackOptions) { o.userUUID = uuid }
}

func WithThreadUUID(uuid string) CallbackOption {
	return func(o *callbackOptions) { o.threadUUID = uuid }
}

func WithUserUUIDResolver(resolve CallbackUUIDResolver) CallbackOption {
	return func(o *callbackOptions) { o.userResolver = resolve }
}

func WithThreadUUIDResolver(resolve CallbackUUIDResolver) CallbackOption {
	return func(o *callbackOptions) { o.threadResolver = resolve }
}

func WithIgnoreRoles(roles ...string) CallbackOption {
	return func(o *callbackOptions) { o.ignoreRoles = append([]string(nil), roles...) }
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
		contextTemplate: DefaultContextTemplate,
		logger:          slog.Default(),
	}
	for _, opt := range opts {
		opt(&resolved)
	}
	return resolved
}

// renderContextTemplate substitutes every occurrence of the literal
// "{context}" placeholder in template with contextBlock via
// [strings.ReplaceAll] — never fmt verbs — so contextBlock or a custom
// template containing "%", "{", or "}" is always safe to inject.
func renderContextTemplate(template, contextBlock string) string {
	return strings.ReplaceAll(template, "{context}", contextBlock)
}

// NewBeforeModelCallback returns an ADK [llmagent.BeforeModelCallback] that
// gives an agent persistent, cross-session memory backed by Zep.
//
// On a genuinely new user turn the callback:
//
//  1. Extracts the user's latest message from the callback context.
//  2. Truncates it to Zep's per-message limit if needed (logging a
//     lengths-only warning; content is never dropped or logged).
//  3. Persists it to the Zep thread whose ID equals the ADK session ID and
//     retrieves the context to inject — either via a single
//     Thread.AddMessages(ReturnContext=true) round-trip (the default), or,
//     when [WithContextBuilder] is configured, by persisting
//     (Thread.AddMessages without ReturnContext) and running the custom
//     [ContextBuilder] concurrently. See [WithContextBuilder] for the
//     error-isolation contract between persistence and the builder.
//  4. Injects the resulting context block into req.Config.SystemInstruction,
//     rendered through the configured template (see [WithContextTemplate],
//     [DefaultContextTemplate]).
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
// callback a no-op — the [ContextBuilder], if any, is never called. Zep
// failures are logged via the configured logger and never propagated, so the
// agent continues without memory rather than crashing.
//
// The application supplies server-generated UUIDs through callback options or
// the zep_user_uuid and zep_thread_uuid state keys.
func NewBeforeModelCallback(client *zepclient.Client, opts ...CallbackOption) llmagent.BeforeModelCallback {
	return newBeforeModelCallback(client, newZepAPI(client), opts...)
}

// newBeforeModelCallback is the seam-friendly core of [NewBeforeModelCallback].
// A nil api makes the callback a no-op. client is threaded through separately
// from api (the testable seam) so [ContextInput.Client] can carry the
// concrete *zepclient.Client without requiring every test to construct one.
func newBeforeModelCallback(client *zepclient.Client, api zepAPI, opts ...CallbackOption) llmagent.BeforeModelCallback {
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

		threadID := resolveCallbackUUID(cc, cfg.threadUUID, cfg.threadResolver, "zep_thread_uuid")
		userUUID := resolveCallbackUUID(cc, cfg.userUUID, cfg.userResolver, "zep_user_uuid")
		latest := LastUserText(cc.UserContent())
		if threadID == "" || userUUID == "" || latest == "" {
			cfg.logger.Warn("zepadk: user or thread UUID is required; proceeding without memory")
			return nil, nil
		}

		truncated := truncateMessageContent(cfg.logger, threadID, latest)
		role := zep.RoleTypeUser
		message := &zep.AddMessage{
			Role:    &role,
			Content: zep.String(truncated),
		}
		if cfg.userName != "" {
			message.Name = zep.String(cfg.userName)
		}

		var contextBlock string
		if cfg.contextBuilder != nil {
			contextBlock = persistAndBuildContext(cc, client, api, cfg, userUUID, threadID, truncated, message, req)
		} else {
			contextBlock = persistWithReturnContext(cc, api, cfg, threadID, message)
		}

		if contextBlock != "" {
			InjectSystemInstruction(req, renderContextTemplate(cfg.contextTemplate, contextBlock))
		}
		return nil, nil
	}
}

// persistWithReturnContext is the default (no custom builder) persist path: a
// single Thread.AddMessages(ReturnContext=true) round-trip. It returns the
// context block to inject, or "" on failure or when Zep returned none.
func persistWithReturnContext(cc agent.CallbackContext, api zepAPI, cfg callbackOptions, threadID string, message *zep.AddMessage) string {
	resp, err := api.AddMessages(cc, threadID, &zep.AddMessagesRequest{
		ReturnContext: zep.Bool(true),
		Messages:      []*zep.AddMessage{message},
		IgnoreRoles:   cfg.ignoreRoles,
	})
	if err != nil {
		// Never crash the host agent on a Zep failure: log and proceed
		// without injecting memory for this turn.
		cfg.logger.Error("zepadk: persisting user message failed; proceeding without memory",
			slog.String("thread_id", threadID), slog.Any("error", err))
		return ""
	}
	if resp != nil && resp.Context != nil {
		return *resp.Context
	}
	return ""
}

// persistAndBuildContext persists the message (Thread.AddMessages without
// ReturnContext) and runs the configured [ContextBuilder] concurrently via two
// goroutines and a sync.WaitGroup, so a slow builder cannot delay persistence
// (or vice versa).
//
// Error isolation (mandatory — see [WithContextBuilder]): each goroutine
// catches and logs its own failure independently, so one side's error can
// neither block nor mask the other's result. It returns the context block to
// inject ("" when the builder failed, returned "", or was skipped).
func persistAndBuildContext(cc agent.CallbackContext, client *zepclient.Client, api zepAPI, cfg callbackOptions, userUUID, threadID, userMessage string, message *zep.AddMessage, req *model.LLMRequest) string {
	var wg sync.WaitGroup
	wg.Add(2)

	go func() {
		defer wg.Done()
		// A panic on a spawned goroutine cannot be recovered upstream and
		// would kill the whole process; recover here to keep the
		// never-crash-the-host-agent contract.
		defer func() {
			if r := recover(); r != nil {
				cfg.logger.Error("zepadk: panic while persisting user message; proceeding without memory",
					slog.String("thread_id", threadID), slog.Any("panic", r))
			}
		}()
		if _, err := api.AddMessages(cc, threadID, &zep.AddMessagesRequest{
			Messages:    []*zep.AddMessage{message},
			IgnoreRoles: cfg.ignoreRoles,
		}); err != nil {
			// Isolated from the builder's outcome: log and proceed. The
			// builder's result (if any) may still be injected below.
			cfg.logger.Error("zepadk: persisting user message failed; proceeding without memory",
				slog.String("thread_id", threadID), slog.Any("error", err))
		}
	}()

	// Written only by the builder goroutine below; safe to read after
	// wg.Wait() synchronizes with that write (no concurrent access).
	var contextBlock string
	go func() {
		defer wg.Done()
		// The builder is arbitrary user code; a panic here would otherwise
		// kill the process (see the persist goroutine above).
		defer func() {
			if r := recover(); r != nil {
				cfg.logger.Warn("zepadk: context builder panicked; skipping context injection for this turn",
					slog.String("thread_id", threadID), slog.Any("panic", r))
			}
		}()
		out, err := cfg.contextBuilder(cc, ContextInput{
			Client:      client,
			UserUUID:    userUUID,
			ThreadUUID:  threadID,
			UserMessage: userMessage,
			Callback:    cc,
			Request:     req,
		})
		if err != nil {
			// Isolated from the persist outcome: log and skip injection.
			// Persistence still completes independently of this failure.
			cfg.logger.Warn("zepadk: context builder failed; skipping context injection for this turn",
				slog.String("thread_id", threadID), slog.Any("error", err))
			return
		}
		contextBlock = out
	}()

	wg.Wait()
	return contextBlock
}

// AfterCallbackOption customizes the behavior of [NewAfterModelCallback].
type AfterCallbackOption func(*afterCallbackOptions)

// afterCallbackOptions holds the resolved configuration for an
// AfterModelCallback.
type afterCallbackOptions struct {
	assistantName string
	threadUUID    string
	resolver      CallbackUUIDResolver
	ignoreRoles   []string
	logger        *slog.Logger
}

func WithAfterThreadUUID(uuid string) AfterCallbackOption {
	return func(o *afterCallbackOptions) { o.threadUUID = uuid }
}

func WithAfterThreadUUIDResolver(resolve CallbackUUIDResolver) AfterCallbackOption {
	return func(o *afterCallbackOptions) { o.resolver = resolve }
}

func WithAfterIgnoreRoles(roles ...string) AfterCallbackOption {
	return func(o *afterCallbackOptions) { o.ignoreRoles = append([]string(nil), roles...) }
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

		threadID := resolveCallbackUUID(cc, cfg.threadUUID, cfg.resolver, "zep_thread_uuid")
		reply := AssistantText(resp.Content)
		if threadID == "" || reply == "" {
			cfg.logger.Warn("zepadk: thread UUID is required; proceeding without memory")
			return nil, nil
		}

		role := zep.RoleTypeAssistant
		message := &zep.AddMessage{
			Role:    &role,
			Content: zep.String(truncateMessageContent(cfg.logger, threadID, reply)),
		}
		if cfg.assistantName != "" {
			message.Name = zep.String(cfg.assistantName)
		}

		if _, err := api.AddMessages(cc, threadID, &zep.AddMessagesRequest{
			Messages:    []*zep.AddMessage{message},
			IgnoreRoles: cfg.ignoreRoles,
		}); err != nil {
			cfg.logger.Error("zepadk: persisting assistant message failed; reply unaffected",
				slog.String("thread_id", threadID), slog.Any("error", err))
		}
		return nil, nil
	}
}

func resolveCallbackUUID(cc agent.CallbackContext, fixed string, resolve CallbackUUIDResolver, stateKey string) string {
	if fixed != "" {
		return fixed
	}
	if resolve != nil {
		if uuid := resolve(cc); uuid != "" {
			return uuid
		}
	}
	if cc == nil || cc.State() == nil {
		return ""
	}
	value, err := cc.State().Get(stateKey)
	if err != nil {
		return ""
	}
	uuid, _ := value.(string)
	return uuid
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

func CreateUser(ctx context.Context, client *zepclient.Client, firstName, lastName, email string) (string, string, error) {
	if client == nil {
		return "", "", fmt.Errorf("zep client is required")
	}
	user, err := client.User.Create(ctx, &zep.CreateUserRequest{
		FirstName: zep.String(firstName),
		LastName:  zep.String(lastName),
		Email:     zep.String(email),
	})
	if err != nil {
		return "", "", err
	}
	if user == nil || user.GetUUID() == nil || user.GetGraphUUID() == nil {
		return "", "", fmt.Errorf("zep: create user returned no UUID")
	}
	return *user.GetUUID(), *user.GetGraphUUID(), nil
}

func CreateThread(ctx context.Context, client *zepclient.Client, userUUID string) (string, error) {
	if client == nil {
		return "", fmt.Errorf("zep client is required")
	}
	thread, err := client.Thread.Create(ctx, &zep.CreateThreadRequest{UserUUID: userUUID})
	if err != nil {
		return "", err
	}
	if thread == nil || thread.GetUUID() == nil {
		return "", fmt.Errorf("zep: create thread returned no UUID")
	}
	return *thread.GetUUID(), nil
}
