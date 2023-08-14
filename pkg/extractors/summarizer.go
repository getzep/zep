package extractors

import (
	"context"
	"fmt"
	"strings"

	llms2 "github.com/tmc/langchaingo/llms"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
)

const SummaryMaxOutputTokens = 1024

// Force compiler to validate that Extractor implements the MemoryStore interface.
var _ models.Extractor = &SummaryExtractor{}

type SummaryExtractor struct {
	BaseExtractor
}

// Extract gets a list of messages created since the last SummaryPoint,
// determines if the message count exceeds the configured message window, and if
// so:
// - determines the new SummaryPoint index, which will one message older than
// message_window / 2
// - summarizes the messages from this new SummaryPoint to the
// oldest message not yet Summarized.
//
// When summarizing, it adds context from these messages to an existing summary
// if there is one.
func (se *SummaryExtractor) Extract(
	ctx context.Context,
	appState *models.AppState,
	messageEvent *models.MessageEvent,
) error {
	sessionID := messageEvent.SessionID
	sessionMutex := se.getSessionMutex(sessionID)
	sessionMutex.Lock()
	defer sessionMutex.Unlock()

	log.Debugf("SummaryExtractor called for session %s", sessionID)

	messageWindow := appState.Config.Memory.MessageWindow

	if messageWindow == 0 {
		return NewExtractorError("SummaryExtractor message window is 0", nil)
	}

	// if no summary exists yet, we'll get all messages
	messagesSummary, err := appState.MemoryStore.GetMemory(
		ctx,
		appState,
		sessionID,
		0,
	)
	if err != nil {
		return NewExtractorError("SummaryExtractor get memory failed", err)
	}

	messages := messagesSummary.Messages
	if messages == nil {
		log.Warningf("SummaryExtractor GetMemory returned no messages for session %s", sessionID)
		return nil
	}
	// If we're still under the message window, we don't need to summarize.
	if len(messages) < appState.Config.Memory.MessageWindow {
		return nil
	}

	newSummary, err := summarize(
		ctx, appState, appState.Config.Memory.MessageWindow, messages, messagesSummary.Summary, 0,
	)
	if err != nil {
		return NewExtractorError("SummaryExtractor summarize failed", err)
	}

	err = appState.MemoryStore.PutSummary(
		ctx,
		appState,
		sessionID,
		newSummary,
	)
	if err != nil {
		return NewExtractorError("SummaryExtractor put summary failed", err)
	}

	log.Debugf("SummaryExtractor completed for session %s", sessionID)

	return nil
}

func (se *SummaryExtractor) Notify(
	ctx context.Context,
	appState *models.AppState,
	messageEvents *models.MessageEvent,
) error {
	if messageEvents == nil {
		return NewExtractorError(
			"SummaryExtractor message events is nil at Notify",
			nil,
		)
	}
	log.Debugf("SummaryExtractor notify: %d messages", len(messageEvents.Messages))
	go func() {
		err := se.Extract(ctx, appState, messageEvents)
		if err != nil {
			log.Error(fmt.Sprintf("SummaryExtractor extract failed: %v", err))
		}
	}()
	return nil
}

func NewSummaryExtractor() *SummaryExtractor {
	return &SummaryExtractor{}
}

// summarize takes a slice of messages and a summary and returns a slice of messages that,
// if larger than the window size, results in the messages slice being halved. If the slice of messages is larger than
// the window size, the summary is updated to reflect the oldest messages that are removed. Expects messages to be in
// chronological order, with the oldest first.
func summarize(
	ctx context.Context,
	appState *models.AppState,
	windowSize int,
	messages []models.Message,
	summary *models.Summary,
	promptTokens int,
) (*models.Summary, error) {
	var currentSummaryContent string
	if summary != nil {
		currentSummaryContent = summary.Content
	}

	// New messages reduced to Half the windowSize to minimize the need to summarize new messages in the future.
	newMessageCount := windowSize / 2

	// Oldest messages that are over the newMessageCount
	messagesToSummarize := messages[:len(messages)-newMessageCount]

	modelName, err := llms.GetLLMModelName(appState.Config)
	if err != nil {
		return &models.Summary{}, err
	}
	maxTokens, ok := llms.MaxLLMTokensMap[modelName]
	if !ok {
		return &models.Summary{}, fmt.Errorf("model name not found in MaxLLMTokensMap")
	}

	if promptTokens == 0 {
		// rough calculation of tokes for current prompt, plus some headroom
		promptTokens = 250
	}

	// We use this to determine how many tokens we can use for the incremental summarization
	// loop. We add more messages to a summarization loop until we hit this.
	summarizerMaxInputTokens := maxTokens - SummaryMaxOutputTokens - promptTokens

	// Take the oldest messages that are over newMessageCount and summarize them.
	newSummary, err := processOverLimitMessages(
		ctx,
		appState,
		messagesToSummarize,
		summarizerMaxInputTokens,
		currentSummaryContent,
	)
	if err != nil {
		return &models.Summary{}, err
	}

	if newSummary.Content == "" {
		return &models.Summary{}, fmt.Errorf(
			"no summary found after summarization",
		)
	}

	return newSummary, nil
}

// processOverLimitMessages takes a slice of messages and a summary and enriches
// the summary with the messages content. Summary can an empty string. Returns a
// Summary model with enriched summary and the number of tokens in the summary.
func processOverLimitMessages(
	ctx context.Context,
	appState *models.AppState,
	messages []models.Message,
	summarizerMaxInputTokens int,
	summary string,
) (*models.Summary, error) {
	var tempMessageText []string //nolint:prealloc
	var newSummary string
	var newSummaryTokens int

	var err error
	totalTokensTemp := 0

	if len(messages) == 0 {
		return nil, fmt.Errorf("no messages to summarize")
	}

	newSummaryPointUUID := messages[len(messages)-1].UUID

	processSummary := func() error {
		newSummary, newSummaryTokens, err = incrementalSummarizer(
			ctx,
			appState,
			summary,
			tempMessageText,
			SummaryMaxOutputTokens,
		)
		if err != nil {
			return err
		}
		tempMessageText = []string{}
		totalTokensTemp = 0
		return nil
	}

	for _, message := range messages {
		messageText := fmt.Sprintf("%s: %s", message.Role, message.Content)
		messageTokens, err := appState.LLMClient.GetTokenCount(messageText)
		if err != nil {
			return nil, err
		}

		if totalTokensTemp+messageTokens > summarizerMaxInputTokens {
			err = processSummary()
			if err != nil {
				return nil, err
			}
		}

		tempMessageText = append(tempMessageText, messageText)
		totalTokensTemp += messageTokens
	}

	if len(tempMessageText) > 0 {
		err = processSummary()
		if err != nil {
			return nil, err
		}
	}

	return &models.Summary{
		Content:          newSummary,
		TokenCount:       newSummaryTokens,
		SummaryPointUUID: newSummaryPointUUID,
	}, nil
}

// incrementalSummarizer takes a slice of messages and a summary, calls the LLM,
// and returns a new summary enriched with the messages content. Summary can be
// an empty string. Returns a string with the new summary and the number of
// tokens in the summary.
func incrementalSummarizer(
	ctx context.Context,
	appState *models.AppState,
	currentSummary string,
	messages []string,
	summaryMaxTokens int,
) (string, int, error) {
	if len(messages) < 1 {
		return "", 0, NewExtractorError("No messages provided", nil)
	}

	messagesJoined := strings.Join(messages, "\n")
	prevSummary := ""
	if currentSummary != "" {
		prevSummary = currentSummary
	}

	promptData := SummaryPromptTemplateData{
		PrevSummary:    prevSummary,
		MessagesJoined: messagesJoined,
	}

	var summaryPromptTemplate string
	switch appState.Config.LLM.Service {
	case "openai":
		summaryPromptTemplate = summaryPromptTemplateOpenAI
	case "anthropic":
		summaryPromptTemplate = summaryPromptTemplateAnthropic
	default:
		return "", 0, fmt.Errorf("unknown LLM service: %s", appState.Config.LLM.Service)
	}

	progressivePrompt, err := internal.ParsePrompt(summaryPromptTemplate, promptData)
	if err != nil {
		return "", 0, err
	}

	summary, err := appState.LLMClient.Call(
		ctx,
		progressivePrompt,
		llms2.WithMaxTokens(summaryMaxTokens),
	)
	if err != nil {
		return "", 0, err
	}

	summary = strings.TrimSpace(summary)

	tokensUsed, err := appState.LLMClient.GetTokenCount(summary)
	if err != nil {
		return "", 0, err
	}

	return summary, tokensUsed, nil
}
