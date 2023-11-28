package tasks

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/pkg/models"
)

var _ models.Task = &MessageSummaryNERTask{}

func NewMessageSummaryNERTask(appState *models.AppState) models.Task {
	return &MessageSummaryNERTask{
		BaseTask: BaseTask{appState: appState},
	}
}

type MessageSummaryNERTask struct {
	BaseTask
}

func (n *MessageSummaryNERTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	ctx, done := context.WithTimeout(ctx, TaskTimeout*time.Second)
	defer done()

	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return errors.New("MessageSummaryNERTask session_id is empty")
	}

	log.Debugf("MessageSummaryNERTask called for session %s", sessionID)

	summary, err := summaryTaskPayloadToSummary(ctx, n.appState, msg)
	if err != nil {
		return fmt.Errorf("MessageEmbedderTask summaryTaskPayloadToSummary failed: %w", err)
	}

	if len(summary.Content) == 0 {
		log.Warnf("MessageSummaryNERTask summary content is empty for session %s", sessionID)
		return nil
	}

	textData := []models.TextData{
		{
			TextUUID: summary.UUID,
			Text:     summary.Content,
			Language: "en",
		},
	}

	nerResponse, err := callNERTask(ctx, n.appState, textData)
	if err != nil {
		return fmt.Errorf("MessageSummaryNERTask extract entities call failed: %w", err)
	}

	nerSummary := extractEntities(nerResponse.Texts[0].Entities)
	// if no entities, don't update the summary
	if len(nerSummary) == 0 {
		return nil
	}

	summaryMetadataUpdate := &models.Summary{
		UUID: summary.UUID,
		Metadata: map[string]interface{}{
			"system": map[string]interface{}{"entities": nerSummary},
		},
	}
	err = n.appState.MemoryStore.UpdateSummary(ctx, sessionID, summaryMetadataUpdate, false)
	if err != nil {
		return fmt.Errorf("MessageSummaryNERTask failed to put summary metadata: %w", err)
	}

	msg.Ack()

	return nil
}
