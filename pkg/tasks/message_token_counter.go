package tasks

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/pkg/models"
)

// Force compiler to validate that our Extractor implements the Extractor interface.
var _ models.Task = &MessageTokenCountTask{}

type MessageTokenCountTask struct {
	appState *models.AppState
}

func (mt *MessageTokenCountTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	ctx, done := context.WithTimeout(ctx, TaskTimeout*time.Second)
	defer done()

	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return errors.New("MessageTokenCountTask session_id is empty")
	}

	log.Debugf("MessageTokenCountTask called for session %s", sessionID)

	msgs, err := messageTaskPayloadToMessages(ctx, mt.appState, msg)
	if err != nil {
		return fmt.Errorf("TokenCountExtractor messageTaskPayloadToMessages failed: %w", err)
	}

	countResult, err := mt.updateTokenCounts(msgs)
	if err != nil {
		return fmt.Errorf("TokenCountExtractor failed to get token count: %w", err)
	}

	if len(countResult) == 0 {
		return nil
	}

	err = mt.appState.MemoryStore.PutMemory(
		ctx,
		mt.appState,
		sessionID,
		&models.Memory{Messages: countResult},
		true,
	)
	if err != nil {
		if errors.Is(err, models.ErrNotFound) {
			log.Warnf("MessageTokenCountTask PutMemory not found. Were the records deleted?")
			// Don't error out
			msg.Ack()
			return nil
		}
		return fmt.Errorf("TokenCountExtractor update messages failed:  %w", err)
	}

	msg.Ack()

	return nil
}

func (mt *MessageTokenCountTask) updateTokenCounts(
	messages []models.Message,
) ([]models.Message, error) {
	var countResult []models.Message //nolint:prealloc

	for _, m := range messages {
		if m.TokenCount != 0 {
			continue
		}
		t, err := mt.appState.LLMClient.GetTokenCount(fmt.Sprintf("%s: %s", m.Role, m.Content))
		if err != nil {
			return nil, err
		}
		m.TokenCount = t
		countResult = append(countResult, m)
	}
	return countResult, nil
}

func (mt *MessageTokenCountTask) HandleError(err error) {
	log.Errorf("MessageTokenCountTask failed: %v", err)
}
