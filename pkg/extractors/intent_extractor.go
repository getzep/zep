package extractors

import (
	"context"
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

	errs := make(chan error, len(messageEvent.Messages))
	var wg sync.WaitGroup

	for _, message := range messageEvent.Messages {
		wg.Add(1)
		go func(message models.Message) {
			defer wg.Done()

			// Populate the template with the message
			data := IntentPromptTemplateData{
				Input: message.Content,
			}

			// Create a prompt with the Message input that needs to be classified
			prompt, err := internal.ParsePrompt(intentPromptTemplate, data)
			if err != nil {
				errs <- NewExtractorError("IntentExtractor: "+err.Error(), err)
				return
			}

			// Send the populated prompt to the language model
			resp, err := llms.RunChatCompletion(ctx, appState, intentMaxTokens, prompt)
			if err != nil {
				errs <- NewExtractorError("IntentExtractor: "+err.Error(), err)
				return
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
				errs <- NewExtractorError(
					"IntentExtractor failed to put message metadata: "+err.Error(),
					err,
				)
				return
			}
		}(message)
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

func (ee *IntentExtractor) Notify(
	ctx context.Context,
	appState *models.AppState,
	messageEvent *models.MessageEvent,
) error {
	if messageEvent == nil {
		return NewExtractorError("IntentExtractor: messageEvent is nil at Notify", nil)
	}

	// Call Extract with the entire message event
	if err := ee.Extract(ctx, appState, messageEvent); err != nil {
		return NewExtractorError("IntentExtractor: Extract Failed", err)
	}

	return nil
}

func NewIntentExtractor() *IntentExtractor {
	return &IntentExtractor{}
}
