package tasks

import (
	"context"
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

const intentMaxTokens = 512

var IntentStringRegex = regexp.MustCompile(`(?i)^\s*intent\W+\s+`)

var _ models.Task = &MessageIntentTask{}

func NewMessageIntentTask(appState *models.AppState) *MessageIntentTask {
	return &MessageIntentTask{
		BaseTask{
			appState: appState,
		},
	}
}

type MessageIntentTask struct {
	BaseTask
}

func (mt *MessageIntentTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	ctx, done := context.WithTimeout(ctx, TaskTimeout*time.Second)
	defer done()

	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return errors.New("MessageIntentTask session_id is empty")
	}

	log.Debugf("MessageIntentTask called for session %s", sessionID)

	messages, err := messageTaskPayloadToMessages(ctx, mt.appState, msg)
	if err != nil {
		return fmt.Errorf("MessageEmbedderTask messageTaskPayloadToMessages failed: %w", err)
	}

	if len(messages) == 0 {
		return fmt.Errorf("MessageIntentTask messageTaskPayloadToMessages returned no messages")
	}

	errs := make(chan error, len(messages))
	var wg sync.WaitGroup

	for _, m := range messages {
		wg.Add(1)
		go func(message models.Message) {
			defer wg.Done()
			mt.processMessage(ctx, mt.appState, message, sessionID, errs)
		}(m)
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
	appState *models.AppState,
	message models.Message,
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

	// Create the intent into the message metadata
	intentResponse := []models.Message{
		{
			UUID: message.UUID,
			Metadata: map[string]interface{}{"system": map[string]interface{}{
				"intent": intentContent},
			},
		},
	}

	// Create the intent into the message metadata
	err = appState.MemoryStore.UpdateMessages(
		ctx,
		sessionID,
		intentResponse,
		true,
		false,
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
