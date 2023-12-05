package tasks

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
)

func dropEmptyMessages(messages []models.Message) []models.Message {
	for i := len(messages) - 1; i >= 0; i-- {
		if strings.TrimSpace(messages[i].Content) == "" {
			messages = append(messages[:i], messages[i+1:]...)
		}
	}
	return messages
}

func summaryTaskPayloadToSummary(
	ctx context.Context,
	appState *models.AppState,
	msg *message.Message,
) (*models.Summary, error) {
	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return nil, fmt.Errorf("summaryTaskPayloadToSummary session_id is empty")
	}

	var task models.MessageSummaryTask
	err := json.Unmarshal(msg.Payload, &task)
	if err != nil {
		return nil, fmt.Errorf("summaryTaskPayloadToSummary unmarshal failed: %w", err)
	}

	summary, err := appState.MemoryStore.GetSummaryByUUID(ctx, sessionID, task.UUID)
	if err != nil {
		return nil, fmt.Errorf("summaryTaskPayloadToSummary get summary failed: %w", err)
	}

	return summary, nil
}

func messageTaskPayloadToMessages(
	ctx context.Context,
	appState *models.AppState,
	msg *message.Message,
) ([]models.Message, error) {
	sessionID := msg.Metadata["session_id"]
	if sessionID == "" {
		return nil, fmt.Errorf("message task missing session_id metadata: %s", msg.UUID)
	}

	var messageTasks []models.MessageTask
	err := json.Unmarshal(msg.Payload, &messageTasks)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal message task payload: %w", err)
	}

	uuids := make([]uuid.UUID, len(messageTasks))
	for i, m := range messageTasks {
		uuids[i] = m.UUID
	}

	messages, err := appState.MemoryStore.GetMessagesByUUID(ctx, sessionID, uuids)
	if err != nil {
		return nil, fmt.Errorf("failed to get messages by uuid: %w", err)
	}

	return messages, err
}
