package tasks

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
)

var _ models.Task = &MessageEmbedderTask{}

type MessageEmbedderTask struct {
	appState *models.AppState
}

func (t *MessageEmbedderTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return fmt.Errorf("MessageEmbedderTask session_id is empty")
	}
	log.Debugf("MessageEmbedderTask called for session %s", sessionID)

	var msgs []models.Message
	err := json.Unmarshal(msg.Payload, &msgs)
	if err != nil {
		return err
	}

	err = t.Process(ctx, sessionID, msgs)
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

	embeddingRecords := make([]models.MessageEmbedding, len(msgs))
	for i, r := range msgs {
		embeddingRecords[i] = models.MessageEmbedding{
			TextUUID:  r.UUID,
			Embedding: embeddings[i],
		}
	}
	err = t.appState.MemoryStore.PutMessageVectors(
		ctx,
		t.appState,
		sessionID,
		embeddingRecords,
	)
	if err != nil {
		return fmt.Errorf("MessageEmbedderTask put message vectors failed: %w", err)
	}
	return nil
}

// messageToStringSlice converts a slice of MessageEmbedding to a slice of strings.
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

func (t *MessageEmbedderTask) HandleError(err error) {
	log.Errorf("MessageEmbedderTask error: %s", err)
}
