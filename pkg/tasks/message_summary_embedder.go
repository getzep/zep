package tasks

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
)

var _ models.Task = &MessageSummaryEmbedderTask{}

func NewMessageSummaryEmbedderTask(appState *models.AppState) *MessageSummaryEmbedderTask {
	return &MessageSummaryEmbedderTask{
		BaseTask{
			appState: appState,
		},
	}
}

type MessageSummaryEmbedderTask struct {
	BaseTask
}

func (t *MessageSummaryEmbedderTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	ctx, done := context.WithTimeout(ctx, TaskTimeout*time.Second)
	defer done()

	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return fmt.Errorf("MessageSummaryEmbedderTask session_id is empty")
	}
	log.Debugf("MessageSummaryEmbedderTask called for session %s", sessionID)

	var task models.MessageSummaryEmbeddingTask
	err := json.Unmarshal(msg.Payload, &task)
	if err != nil {
		return fmt.Errorf("MessageSummaryEmbeddingTask unmarshal failed: %w", err)
	}

	summary, err := t.appState.MemoryStore.GetSummaryByUUID(ctx, t.appState, sessionID, task.UUID)
	if err != nil {
		return fmt.Errorf("MessageSummaryEmbeddingTask get summary failed: %w", err)
	}

	err = t.Process(ctx, sessionID, summary)
	if err != nil {
		return err
	}

	msg.Ack()

	return nil
}

func (t *MessageSummaryEmbedderTask) Process(
	ctx context.Context,
	sessionID string,
	summary *models.Summary,
) error {
	messageType := "summary"

	if summary.Content == "" {
		return fmt.Errorf(
			"MessageSummaryEmbedderTask summary content is empty for %s",
			summary.UUID,
		)
	}

	model, err := llms.GetEmbeddingModel(t.appState, messageType)
	if err != nil {
		return fmt.Errorf("MessageSummaryEmbedderTask get message embedding model failed: %w", err)
	}

	embeddings, err := llms.EmbedTexts(
		ctx,
		t.appState,
		model,
		messageType,
		[]string{summary.Content},
	)
	if err != nil {
		return fmt.Errorf("MessageSummaryEmbedderTask embed messages failed: %w", err)
	}

	record := &models.TextEmbedding{
		Embedding: embeddings[0],
		TextUUID:  summary.UUID,
		Text:      summary.Content,
	}
	err = t.appState.MemoryStore.PutSummaryEmbedding(
		ctx,
		t.appState,
		sessionID,
		record,
	)
	if err != nil {
		return fmt.Errorf("MessageSummaryEmbedderTask put embeddings failed: %w", err)
	}
	return nil
}

func (t *MessageSummaryEmbedderTask) HandleError(err error) {
	log.Errorf("MessageSummaryEmbedderTask error: %s", err)
}
