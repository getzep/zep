package tasks

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/tmc/langchaingo/llms"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
)

var _ models.Task = &MessageIntentTask{}

type MessageIntentTask struct {
	appState *models.AppState
}

const intentMaxTokens = 512

var IntentStringRegex = regexp.MustCompile(`(?i)^\s*intent\W+\s+`)

func (mt *MessageIntentTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	ctx, done := context.WithTimeout(ctx, TaskTimeout*time.Second)
	defer done()

	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return errors.New("NERTask session_id is empty")
	}

	log.Debugf("NERTask called for session %s", sessionID)

	var msgs []models.Message
	err := json.Unmarshal(msg.Payload, &msgs)
	if err != nil {
		return err
	}

	errs := make(chan error, len(msgs))
	var wg sync.WaitGroup

	for _, message := range msgs {
		wg.Add(1)
		go func(message models.Message) {
			defer wg.Done()
			mt.processMessage(ctx, message, mt.appState, sessionID, errs)
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
		return fmt.Errorf(
			"MessageIntentTask: Extract Failed %w",
			errors.New(strings.Join(errStrings, "; ")),
		)
	}

	msg.Ack()

	return nil
}

func (mt *MessageIntentTask) processMessage(
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
		errs <- fmt.Errorf("MessageIntentTask: %w", err)
		return
	}

	// Send the populated prompt to the language model
	intentContent, err := appState.LLMClient.Call(
		ctx,
		prompt,
		llms.WithMaxTokens(intentMaxTokens),
	)
	if err != nil {
		errs <- fmt.Errorf("MessageIntentTask: %w", err)
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
	err = appState.MemoryStore.PutMessageMetadata(
		ctx,
		appState,
		sessionID,
		intentResponse,
		true,
	)
	if err != nil {
		if errors.Is(err, models.ErrNotFound) {
			log.Warnf("MessageIntentTask PutMessageMetadata not found. Were the records deleted?")
			// Don't error out
			return
		}
		errs <- fmt.Errorf("MessageIntentTask failed to put message metadata: %w", err)
	}
}

func (mt *MessageIntentTask) HandleError(err error) {
	log.Errorf("MessageIntentTask error: %v", err)
}
