package tasks

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
)

var _ models.Task = &MessageNERTask{}

func NewMessageNERTask(appState *models.AppState) models.Task {
	return &MessageNERTask{
		BaseTask: BaseTask{appState: appState},
	}
}

type MessageNERTask struct {
	BaseTask
}

func (n *MessageNERTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	ctx, done := context.WithTimeout(ctx, TaskTimeout*time.Second)
	defer done()

	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return errors.New("MessageNERTask session_id is empty")
	}

	log.Debugf("MessageNERTask called for session %s", sessionID)

	messages, err := messageTaskPayloadToMessages(ctx, n.appState, msg)
	if err != nil {
		return fmt.Errorf("MessageEmbedderTask messageTaskPayloadToMessages failed: %w", err)
	}

	if len(messages) == 0 {
		return fmt.Errorf("MessageNERTask messageTaskPayloadToMessages returned no messages")
	}

	var textData = make([]models.TextData, len(messages))
	for i, m := range messages {
		textData[i] = models.TextData{
			TextUUID: m.UUID,
			Text:     m.Content,
			Language: "en",
		}
	}

	nerResponse, err := callNERTask(ctx, n.appState, textData)
	if err != nil {
		return fmt.Errorf("MessageNERTask extract entities call failed: %w", err)
	}

	nerMessages := make([]models.Message, len(nerResponse.Texts))
	for i, r := range nerResponse.Texts {
		msgUUID, err := uuid.Parse(r.UUID)
		if err != nil {
			return fmt.Errorf("MessageNERTask failed to parse message UUID: %w", err)
		}
		entityList := extractEntities(r.Entities)

		nerMessages[i] = models.Message{
			UUID: msgUUID,
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{"entities": entityList},
			},
		}
	}

	err = n.appState.MemoryStore.UpdateMessages(ctx, sessionID, nerMessages, true, false)
	if err != nil {
		if errors.Is(err, models.ErrNotFound) {
			log.Warnf("MessageNERTask PutMessageMetadata not found. Were the records deleted?")
			// Don't error out
			msg.Ack()
			return nil
		}
		return fmt.Errorf("MessageNERTask failed to put message metadata: %w", err)
	}

	msg.Ack()

	return nil
}
