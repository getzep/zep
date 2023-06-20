package extractors

import (
	"context"
	"strings"

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

	for _, message := range messageEvent.Messages {

		// Populate the template with the message
		data := IntentPromptTemplateData{
			Input: message.Content,
		}

		prompt, err := internal.ParsePrompt(intentPromptTemplate, data)
		if err != nil {
			return NewExtractorError("IntentExtractor: "+err.Error(), err)
		}

		// Send the populated prompt to the language model
		resp, err := llms.RunChatCompletion(ctx, appState, intentMaxTokens, prompt)

		if err != nil {
			return NewExtractorError("IntentExtractor: "+err.Error(), err)
		}

		intentContent := resp.Choices[0].Message.Content
		intentContent = strings.TrimPrefix(intentContent, "Intent: ")

		intentResponse := []models.MessageMetadata{
			{
				UUID:     message.UUID,
				Metadata: map[string]interface{}{"system": map[string]interface{}{"intent": intentContent}},
			},
		}

		log.Infof("IntentExtractor: intentResponse: %+v", intentResponse)
		err = appState.MemoryStore.PutMessageMetadata(ctx, appState, sessionID, intentResponse, true)
		if err != nil {
			return NewExtractorError("IntentExtractor failed to put message metadata: "+err.Error(), err)
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

	go func() {
		err := ee.Extract(ctx, appState, messageEvent)
		if err != nil {
			log.Errorf("IntentExtractor: Extract Failed: %v", err)
		}
	}()
	return nil
}

func NewIntentExtractor() *IntentExtractor {
	return &IntentExtractor{}
}
