package tasks

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
)

var _ models.Task = &MessageEmbedderTask{}

func NewMessageEmbedderTask(appState *models.AppState) *MessageEmbedderTask {
	return &MessageEmbedderTask{
		BaseTask: BaseTask{
			appState: appState,
		},
	}
}

type MessageEmbedderTask struct {
	BaseTask
}

func (t *MessageEmbedderTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	ctx, done := context.WithTimeout(ctx, TaskTimeout*time.Second)
	defer done()

	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return fmt.Errorf("MessageEmbedderTask session_id is empty")
	}
	log.Debugf("MessageEmbedderTask called for session %s", sessionID)

	messages, err := messageTaskPayloadToMessages(ctx, t.appState, msg)
	if err != nil {
		return fmt.Errorf("MessageEmbedderTask messageTaskPayloadToMessages failed: %w", err)
	}

	if len(messages) == 0 {
		return fmt.Errorf("MessageEmbedderTask messageTaskPayloadToMessages returned no messages")
	}

	err = t.Process(ctx, sessionID, messages)
	if err != nil {
		return err
	}

	msg.Ack()

	return nil
}

func (t *MessageEmbedderTask) Process(
	ctx context.Context,
	sessionID string,
	msgs []models.Message,
) error {
	messageType := "message"
	texts := messageToStringSlice(msgs, false)

	model, err := llms.GetEmbeddingModel(t.appState, messageType)
	if err != nil {
		return fmt.Errorf("MessageEmbedderTask get message embedding model failed: %w", err)
	}

	embeddings, err := llms.EmbedTexts(ctx, t.appState, model, messageType, texts)
	if err != nil {
		return fmt.Errorf("MessageEmbedderTask embed messages failed: %w", err)
	}

	embeddingRecords := make([]models.TextData, len(msgs))
	for i, r := range msgs {
		embeddingRecords[i] = models.TextData{
			TextUUID:  r.UUID,
			Embedding: embeddings[i],
		}
	}
	err = t.appState.MemoryStore.CreateMessageEmbeddings(
		ctx,
		sessionID,
		embeddingRecords,
	)
	if err != nil {
		if errors.Is(err, models.ErrNotFound) {
			log.Warnf(
				"MessageEmbedderTask CreateMessageEmbeddings not found. Were the records deleted? %v",
				err,
			)
			// Don't error out
			return nil
		}
		return fmt.Errorf("MessageEmbedderTask put message vectors failed: %w", err)
	}
	return nil
}

// messageToStringSlice converts a slice of TextData to a slice of strings.
// If enrich is true, the text slice will include the prior and subsequent
// messages text to the slice item.
func messageToStringSlice(messages []models.Message, enrich bool) []string {
	texts := make([]string, len(messages))
	for i, r := range messages {
		if !enrich {
			texts[i] = r.Content
			continue
		}

		var parts []string

		if i > 0 {
			parts = append(parts, messages[i-1].Content)
		}

		parts = append(parts, r.Content)

		if i < len(messages)-1 {
			parts = append(parts, messages[i+1].Content)
		}

		texts[i] = strings.Join(parts, " ")
	}
	return texts
}
