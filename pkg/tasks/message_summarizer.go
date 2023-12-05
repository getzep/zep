package tasks

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	llms2 "github.com/tmc/langchaingo/llms"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
)

const MaxTokensFallback = 2048
const SummaryMaxOutputTokens = 1024

var _ models.Task = &MessageSummaryTask{}

// MessageSummaryTask gets a list of messages created since the last SummaryPoint,
// determines if the message count exceeds the configured message window, and if
// so:
// - determines the new SummaryPoint index, which will one message older than
// message_window / 2
// - summarizes the messages from this new SummaryPoint to the
// oldest message not yet Summarized.
//
// When summarizing, it adds context from these messages to an existing summary
// if there is one.
type MessageSummaryTask struct {
	BaseTask
}

func NewMessageSummaryTask(appState *models.AppState) *MessageSummaryTask {
	return &MessageSummaryTask{
		BaseTask: BaseTask{
			appState: appState,
		},
	}
}

func (t *MessageSummaryTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	ctx, done := context.WithTimeout(ctx, TaskTimeout*time.Second)
	defer done()

	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return errors.New("SummaryTask session_id is empty")
	}

	log.Debugf("SummaryTask called for session %s", sessionID)

	messageWindow := t.appState.Config.Memory.MessageWindow
	if messageWindow == 0 {
		return errors.New("SummaryTask message window is 0")
	}

	// if no summary exists yet, we'll get all messages up to the message window
	messagesSummary, err := t.appState.MemoryStore.GetMemory(
		ctx,
		sessionID,
		0,
	)
	if err != nil {
		return fmt.Errorf("SummaryTask get memory failed: %w", err)
	}

	messages := messagesSummary.Messages
	if messages == nil {
		log.Warningf("SummaryTask GetMemory returned no messages for session %s", sessionID)
		return nil
	}

	// drop empty messages
	messages = dropEmptyMessages(messages)

	// If we're still under the message window, we don't need to summarize.
	if len(messages) < t.appState.Config.Memory.MessageWindow {
		return nil
	}

	newSummary, err := t.summarize(
		ctx, messages, messagesSummary.Summary, 0,
	)
	if err != nil {
		return fmt.Errorf("SummaryTask summarize failed %w", err)
	}

	err = t.appState.MemoryStore.CreateSummary(
		ctx,
		sessionID,
		newSummary,
	)
	if err != nil {
		if errors.Is(err, models.ErrNotFound) {
			log.Warnf("MessageSummaryTask CreateSummary not found. Were the records deleted?")
			// Don't error out
			msg.Ack()
			return nil
		}
		return fmt.Errorf("SummaryTask put summary failed: %w", err)
	}

	log.Debugf("SummaryTask completed for session %s", sessionID)

	msg.Ack()

	return nil
}

func (t *MessageSummaryTask) HandleError(err error) {
	log.Errorf("SummaryExtractor failed: %v", err)
}

// summarize takes a slice of messages and a summary and returns a slice of messages that,
// if larger than the window size, results in the messages slice being halved. If the slice of messages is larger than
// the window size, the summary is updated to reflect the oldest messages that are removed. Expects messages to be in
// chronological order, with the oldest first.
func (t *MessageSummaryTask) summarize(
	ctx context.Context,
	messages []models.Message,
	summary *models.Summary,
	promptTokens int,
) (*models.Summary, error) {
	var currentSummaryContent string
	if summary != nil {
		currentSummaryContent = summary.Content
	}

	// New messages reduced to Half the MessageWindow to minimize the need to summarize new messages in the future.
	newMessageCount := t.appState.Config.Memory.MessageWindow / 2

	// Oldest messages that are over the newMessageCount
	messagesToSummarize := messages[:len(messages)-newMessageCount]

	modelName, err := llms.GetLLMModelName(t.appState.Config)
	if err != nil {
		return &models.Summary{}, err
	}
	maxTokens, ok := llms.MaxLLMTokensMap[modelName]
	if !ok {
		maxTokens = MaxTokensFallback
	}

	if promptTokens == 0 {
		// rough calculation of tokes for current prompt, plus some headroom
		promptTokens = 250
	}

	// We use this to determine how many tokens we can use for the incremental summarization
	// loop. We add more messages to a summarization loop until we hit this.
	summarizerMaxInputTokens := maxTokens - SummaryMaxOutputTokens - promptTokens

	// Take the oldest messages that are over newMessageCount and summarize them.
	newSummary, err := t.processOverLimitMessages(
		ctx,
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
func (t *MessageSummaryTask) processOverLimitMessages(
	ctx context.Context,
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
		newSummary, newSummaryTokens, err = t.incrementalSummarizer(
			ctx,
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

	for _, m := range messages {
		messageText := fmt.Sprintf("%s: %s", m.Role, m.Content)
		messageTokens, err := t.appState.LLMClient.GetTokenCount(messageText)
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

func (t *MessageSummaryTask) validateSummarizerPrompt(prompt string) error {
	prevSummaryIdentifier := "{{.PrevSummary}}"
	messagesJoinedIdentifier := "{{.MessagesJoined}}"

	isCustomPromptValid := strings.Contains(prompt, prevSummaryIdentifier) &&
		strings.Contains(prompt, messagesJoinedIdentifier)

	if !isCustomPromptValid {
		return fmt.Errorf(
			"wrong summary prompt format. please make sure it contains the identifiers %s and %s",
			prevSummaryIdentifier, messagesJoinedIdentifier,
		)
	}
	return nil
}

// incrementalSummarizer takes a slice of messages and a summary, calls the LLM,
// and returns a new summary enriched with the messages content. Summary can be
// an empty string. Returns a string with the new summary and the number of
// tokens in the summary.
func (t *MessageSummaryTask) incrementalSummarizer(
	ctx context.Context,
	currentSummary string,
	messages []string,
	summaryMaxTokens int,
) (string, int, error) {
	if len(messages) < 1 {
		return "", 0, errors.New("no messages provided")
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

	progressivePrompt, err := t.generateProgressiveSummarizerPrompt(promptData)
	if err != nil {
		return "", 0, err
	}

	summary, err := t.appState.LLMClient.Call(
		ctx,
		progressivePrompt,
		llms2.WithMaxTokens(summaryMaxTokens),
	)
	if err != nil {
		return "", 0, err
	}

	summary = strings.TrimSpace(summary)

	tokensUsed, err := t.appState.LLMClient.GetTokenCount(summary)
	if err != nil {
		return "", 0, err
	}

	return summary, tokensUsed, nil
}

func (t *MessageSummaryTask) generateProgressiveSummarizerPrompt(
	promptData SummaryPromptTemplateData,
) (string, error) {
	customSummaryPromptTemplateAnthropic := t.appState.Config.CustomPrompts.SummarizerPrompts.Anthropic
	customSummaryPromptTemplateOpenAI := t.appState.Config.CustomPrompts.SummarizerPrompts.OpenAI

	var summaryPromptTemplate string
	switch t.appState.Config.LLM.Service {
	case "openai":
		if customSummaryPromptTemplateOpenAI != "" {
			summaryPromptTemplate = customSummaryPromptTemplateOpenAI
		} else {
			summaryPromptTemplate = defaultSummaryPromptTemplateOpenAI
		}
	case "anthropic":
		if customSummaryPromptTemplateAnthropic != "" {
			summaryPromptTemplate = customSummaryPromptTemplateAnthropic
		} else {
			summaryPromptTemplate = defaultSummaryPromptTemplateAnthropic
		}
	default:
		return "", fmt.Errorf("unknown LLM service: %s", t.appState.Config.LLM.Service)
	}

	err := t.validateSummarizerPrompt(summaryPromptTemplate)
	if err != nil {
		return "", err
	}

	return internal.ParsePrompt(summaryPromptTemplate, promptData)
}
