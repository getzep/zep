package extractors

import (
	"context"
	"fmt"
	"strings"
	"sync"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
)

var _ models.Extractor = &IntentExtractor{}

const intentMaxTokens = 512

type IntentExtractor struct {
	BaseExtractor
}

func (ee *IntentExtractor) Extract(
	ctx context.Context,
	appState *models.AppState,
	messageEvent *models.MessageEvent,
) error {
	sessionID := messageEvent.SessionID
	sessionMutex := ee.getSessionMutex(sessionID)
	sessionMutex.Lock()
	defer sessionMutex.Unlock()

	// Sanity checker: Check if there's exactly one message in messageEvent
	if len(messageEvent.Messages) != 1 {
		return NewExtractorError(
			fmt.Sprintf("IntentExtractor: expected 1 message, got %d", len(messageEvent.Messages)),
			nil,
		)
	}

	// As we only have one message in messageEvent, we can directly use it. The iterator is in Notify.
	message := messageEvent.Messages[0]

	// Populate the template with the message
	data := IntentPromptTemplateData{
		Input: message.Content,
	}

	// Create a prompt with the Message input that needs to be classified
	prompt, err := internal.ParsePrompt(intentPromptTemplate, data)
	if err != nil {
		return NewExtractorError("IntentExtractor: "+err.Error(), err)
	}

	// Send the populated prompt to the language model
	resp, err := llms.RunChatCompletion(ctx, appState, intentMaxTokens, prompt)
	if err != nil {
		return NewExtractorError("IntentExtractor: "+err.Error(), err)
	}

	// Get the intent from the response
	intentContent := resp.Choices[0].Message.Content
	intentContent = strings.TrimPrefix(intentContent, "Intent: ")

	// Put the intent into the message metadata
	intentResponse := []models.MessageMetadata{
		{
			UUID: message.UUID,
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{"intent": intentContent},
			},
		},
	}

	// Put the intent into the message metadata
	log.Debugf("IntentExtractor: intentResponse: %+v", intentResponse)
	err = appState.MemoryStore.PutMessageMetadata(ctx, appState, sessionID, intentResponse, true)
	if err != nil {
		return NewExtractorError(
			"IntentExtractor failed to put message metadata: "+err.Error(),
			err,
		)
	}

	return nil
}

func (ee *IntentExtractor) Notify(
	ctx context.Context,
	appState *models.AppState,
	messageEvent *models.MessageEvent,
) error {
	if messageEvent == nil {
		return NewExtractorError("IntentExtractor: messageEvent is nil at Notify", nil)
	}

	var wg sync.WaitGroup
	errs := make(chan error, len(messageEvent.Messages))

	// Speed up the intent extraction by calling go routines for each message
	// useful for batch message intent extraction
	for _, message := range messageEvent.Messages {
		// Create a new copy of message to avoid data race
		message := message

		// Create a new single message event
		singleMessageEvent := &models.MessageEvent{
			SessionID: messageEvent.SessionID,
			Messages:  []models.Message{message},
		}

		// Increment the WaitGroup counter
		wg.Add(1)

		// Extract the intent in a goroutine
		go func() {
			defer wg.Done()
			if err := ee.Extract(ctx, appState, singleMessageEvent); err != nil {
				errs <- err
			}
		}()
	}

	// Wait for all goroutines to finish
	wg.Wait()
	close(errs)

	// Check if we got any errors
	for err := range errs {
		if err != nil {
			return NewExtractorError("IntentExtractor: Extract Failed", err)
		}
	}

	return nil
}

func NewIntentExtractor() *IntentExtractor {
	return &IntentExtractor{}
}
