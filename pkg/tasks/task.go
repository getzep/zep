package tasks

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
)

const (
	MessageSummarizerTopic      = "message_summarizer"
	MessageEmbedderTopic        = "message_embedder"
	MessageNerTopic             = "message_ner"
	MessageIntentTopic          = "message_intent"
	MessageTokenCountTopic      = "message_token_count"
	DocumentEmbedderTopic       = "document_embedder"
	MessageSummaryEmbedderTopic = "message_summary_embedder"
)

var log = internal.GetLogger()

func Initialize(ctx context.Context, appState *models.AppState, router models.TaskRouter) {
	log.Info("Initializing tasks")

	addTask := func(ctx context.Context, name, taskType string, enabled bool, newTask func() models.Task) {
		if enabled {
			task := newTask()
			router.AddTask(ctx, name, taskType, task)
			log.Infof("%s task added to task router", name)
		}
	}

	addTask(
		ctx,
		MessageSummarizerTopic,
		MessageSummarizerTopic,
		appState.Config.Extractors.Messages.Summarizer.Enabled,
		func() models.Task { return &MessageSummaryTask{appState: appState} },
	)

	addTask(
		ctx,
		MessageEmbedderTopic,
		MessageEmbedderTopic,
		appState.Config.Extractors.Messages.Embeddings.Enabled,
		func() models.Task { return &MessageEmbedderTask{appState: appState} },
	)

	addTask(
		ctx,
		MessageNerTopic,
		MessageNerTopic,
		appState.Config.Extractors.Messages.Entities.Enabled,
		func() models.Task { return &MessageNERTask{appState: appState} },
	)

	addTask(
		ctx,
		MessageIntentTopic,
		MessageIntentTopic,
		appState.Config.Extractors.Messages.Intent.Enabled,
		func() models.Task { return &MessageIntentTask{appState: appState} },
	)

	addTask(
		ctx,
		MessageTokenCountTopic,
		MessageTokenCountTopic,
		true, // Always enabled
		func() models.Task { return &MessageTokenCountTask{appState: appState} },
	)

	addTask(
		ctx,
		DocumentEmbedderTopic,
		DocumentEmbedderTopic,
		appState.Config.Extractors.Documents.Embeddings.Enabled,
		func() models.Task { return &DocumentEmbedderTask{appState: appState} },
	)

	addTask(
		ctx,
		MessageSummaryEmbedderTopic,
		MessageSummaryEmbedderTopic,
		appState.Config.Extractors.Messages.Summarizer.Embeddings.Enabled,
		func() models.Task { return &MessageSummaryEmbedderTask{appState: appState} },
	)

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

	messages, err := appState.MemoryStore.GetMessagesByUUID(ctx, appState, sessionID, uuids)
	if err != nil {
		return nil, fmt.Errorf("failed to get messages by uuid: %w", err)
	}

	return messages, err
}
