package extractors

import (
	"context"
	"errors"
	"regexp"
	"strings"
	"sync"

	"github.com/tmc/langchaingo/llms"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
)

var _ models.Extractor = &IntentExtractor{}

const intentMaxTokens = 512

var IntentStringRegex = regexp.MustCompile(`(?i)^\s*intent\W+\s+`)

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
			ee.processMessage(ctx, message, appState, sessionID, errs)
		}(message)
	}

	// Create a goroutine to close errs after wg is done
	go func() {
		wg.Wait()
		close(errs)
	}()

	// Initialize variables for collecting multiple errors
	var errStrings []string
	var hasErrors bool

	// Check if we got any errors and collect all errors.
	// This will loop until errs is closed..
	for err := range errs {
		if err != nil {
			hasErrors = true
			errStrings = append(errStrings, err.Error())
		}
	}

	// Return combined errors strings if hasErrors is set to true
	if hasErrors {
		return NewExtractorError(
			"IntentExtractor: Extract Failed",
			errors.New(strings.Join(errStrings, "; ")),
		)
	}

	return nil
}

func (ee *IntentExtractor) processMessage(
	ctx context.Context,
	message models.Message,
	appState *models.AppState,
	sessionID string,
	errs chan error,
) {
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
	intentContent, err := appState.LLMClient.Call(
		ctx,
		prompt,
		llms.WithMaxTokens(intentMaxTokens),
	)
	if err != nil {
		errs <- NewExtractorError("IntentExtractor: "+err.Error(), err)
		return
	}

	// Get the intent from the response
	intentContent = IntentStringRegex.ReplaceAllStringFunc(intentContent, func(s string) string {
		return ""
	})

	// if we don't have an intent, just return
	if intentContent == "" {
		return
	}

	// Put the intent into the message metadata
	intentResponse := []models.Message{
		{
			UUID: message.UUID,
			Metadata: map[string]interface{}{"system": map[string]interface{}{
				"intent": intentContent},
			},
		},
	}

	// Put the intent into the message metadata
	log.Debugf("IntentExtractor: intentResponse: %+v", intentResponse)
	err = appState.MemoryStore.PutMessageMetadata(
		ctx,
		appState,
		sessionID,
		intentResponse,
		true,
	)
	if err != nil {
		errs <- NewExtractorError(
			"IntentExtractor failed to put message metadata: "+err.Error(),
			err,
		)
	}
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
