package extractors

import (
	"context"
	"fmt"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/spf13/viper"
	"sync"
)

// TODO: What are we doing with token count. Appears we should be incrementing it?

const SummaryMaxOutputTokens = 512

// Force compiler to validate that RedisMemoryStore implements the MemoryStore interface.
var _ models.Extractor = &MaxMessageWindowSummaryExtractor{}

type MaxMessageWindowSummaryExtractor struct {
	models.BaseExtractor
}

func (se *MaxMessageWindowSummaryExtractor) Extract(
	ctx context.Context,
	appState *models.AppState,
	messageEvent *models.MessageEvent,
) error {
	messageWindow := viper.GetInt("memory.message_window")
	if messageWindow == 0 {
		return NewExtractorError("MaxMessageWindowSummaryExtractor message window is 0", nil)
	}
	memoryStore := appState.MemoryStore

	// Lock session to avoid a prune race condition where new messages are added to the session
	// while we are generating a summary. We may over prune the session if not.
	sessionLock, _ := appState.SessionLock.LoadOrStore(messageEvent.SessionID, &sync.Mutex{})
	sessionLockMutex := sessionLock.(*sync.Mutex)
	sessionLockMutex.Lock()
	defer sessionLockMutex.Unlock()

	messagesSummary, err := memoryStore.GetMemory(ctx, appState, messageEvent.SessionID, 0, 0)
	if err != nil {
		return NewExtractorError("MaxMessageWindowSummaryExtractor get memory failed", err)
	}

	summary := messagesSummary.Summary
	messages := messagesSummary.Messages

	_, newMessagesSize, newSummary, err := summarizeToMaxMessageWindowSize(
		ctx,
		appState,
		messageWindow,
		&messages,
		&summary,
		-1,
	)
	if err != nil {
		return NewExtractorError("MaxMessageWindowSummaryExtractor summarize failed", err)
	}

	err = memoryStore.PutSummary(ctx, appState, messageEvent.SessionID, newSummary)
	if err != nil {
		return NewExtractorError("MaxMessageWindowSummaryExtractor put summary failed", err)
	}

	err = memoryStore.PruneSession(
		ctx,
		appState,
		messageEvent.SessionID,
		int64(newMessagesSize),
		false, // don't lock on prune here as we do so above
	)
	if err != nil {
		return NewExtractorError("MaxMessageWindowSummaryExtractor prune session failed", err)
	}

	return nil
}

func (se *MaxMessageWindowSummaryExtractor) Notify(
	ctx context.Context,
	appState *models.AppState,
	messageEvents *models.MessageEvent,
) error {
	log.Debugf("MaxMessageWindowSummaryExtractor notify: %v", messageEvents)
	if messageEvents == nil {
		return NewExtractorError(
			"MaxMessageWindowSummaryExtractor message events is nil at Notify",
			nil,
		)
	}
	go func() {
		err := se.Extract(ctx, appState, messageEvents)
		if err != nil {
			log.Error(fmt.Sprintf("MaxMessageWindowSummaryExtractor extract failed: %v", err))
		}
	}()
	return nil
}

func (se *MaxMessageWindowSummaryExtractor) ListenForEvents(
	_ context.Context,
	_ *models.AppState,
) error {
	return fmt.Errorf("not implemented yet")
}

func NewMaxMessageWindowSummaryExtractor(
	appState *models.AppState,
) *MaxMessageWindowSummaryExtractor {
	return &MaxMessageWindowSummaryExtractor{}
}

// summarizeToMaxMessageWindowSize takes a slice of messages and a summary and returns a slice of messages that,
// if larger than the window size, results in the messages slice being halved. If the slice of messages is larger than
// the window size, the summary is updated to reflect the oldest messages that are removed. Expects messages to be in
// reverse chronological order, with the oldest first.
// TODO: Determine whether the message slice needs to be reversed.
func summarizeToMaxMessageWindowSize(
	ctx context.Context,
	appState *models.AppState,
	windowSize int,
	messages *[]models.Message,
	summary *models.Summary,
	promptTokens int,
) (*[]models.Message, int, *models.Summary, error) {
	if len(*messages) < windowSize {
		return messages, len(*messages), summary, nil
	}

	// New messages reduced to Half the windowSize to minimize the need to summarize new messages in the future.
	newMessageCount := windowSize / 2

	modelName, err := llms.GetLLMModelName()
	if err != nil {
		return nil, 0, &models.Summary{}, err
	}
	maxTokens, ok := llms.MaxLLMTokensMap[modelName]
	if !ok {
		return nil, 0, &models.Summary{}, fmt.Errorf("model name not found in MaxLLMTokensMap")
	}

	if promptTokens < 0 {
		// rough calculation of tokes for current prompt, plus some headroom
		promptTokens = 300
	}

	summarizerMaxInputTokens := maxTokens - SummaryMaxOutputTokens - promptTokens

	newSummary, err := processOverLimitMessages(
		ctx,
		appState,
		(*messages)[:len(*messages)-newMessageCount],
		summarizerMaxInputTokens,
		summary.Content,
	)
	if err != nil {
		return nil, 0, &models.Summary{}, err
	}

	newMessageList := (*messages)[len(*messages)-newMessageCount:]

	if newSummary == "NONE" || newSummary == "" {
		fmt.Println(newSummary)
		return &newMessageList, newMessageCount, &models.Summary{}, fmt.Errorf(
			"no summary found after summarization",
		)
	}

	return &newMessageList, newMessageCount, &models.Summary{Content: newSummary}, nil
}

// processOverLimitMessages takes a slice of messages and a summary and enriches the summary with the messages
// content.
func processOverLimitMessages(
	ctx context.Context,
	appState *models.AppState,
	overLimitMessages []models.Message,
	summarizerMaxInputTokens int,
	summary string,
) (string, error) {
	var tempMessagesContent []string //nolint:prealloc
	var newSummary string
	var err error
	totalTokensTemp := 0

	processSummary := func() error {
		newSummary, _, err = incrementalSummarizer(ctx, appState.OpenAIClient, summary,
			tempMessagesContent, SummaryMaxOutputTokens)
		if err != nil {
			return err
		}
		tempMessagesContent = []string{}
		totalTokensTemp = 0
		return nil
	}

	for _, message := range overLimitMessages {
		messageContent := message.Content
		messageTokens, err := llms.GetTokenCount(messageContent)
		if err != nil {
			return "", err
		}

		if totalTokensTemp+messageTokens > summarizerMaxInputTokens {
			err = processSummary()
			if err != nil {
				return "", err
			}
		}

		tempMessagesContent = append(tempMessagesContent, messageContent)
		totalTokensTemp += messageTokens
	}

	if len(tempMessagesContent) > 0 {
		err = processSummary()
		if err != nil {
			return "", err
		}
	}

	return newSummary, nil
}
