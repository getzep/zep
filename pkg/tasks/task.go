package tasks

import (
	"context"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
)

var log = internal.GetLogger()

type BaseTask struct {
	appState *models.AppState // nolint: unused
}

func (b *BaseTask) Execute(
	_ context.Context,
	_ *message.Message,
) error {
	return nil
}

func (b *BaseTask) HandleError(err error) {
	log.Errorf("Task HandleError error: %s", err)
}

func Initialize(ctx context.Context, appState *models.AppState, router models.TaskRouter) {
	log.Info("Initializing tasks")

	addTask := func(ctx context.Context, name string, taskType models.TaskTopic, enabled bool, newTask func() models.Task) {
		if enabled {
			task := newTask()
			router.AddTask(ctx, name, taskType, task)
			log.Infof("%s task added to task router", name)
		}
	}

	addTask(
		ctx,
		string(models.MessageSummarizerTopic),
		models.MessageSummarizerTopic,
		appState.Config.Extractors.Messages.Summarizer.Enabled,
		func() models.Task { return NewMessageSummaryTask(appState) },
	)

	addTask(
		ctx,
		string(models.MessageEmbedderTopic),
		models.MessageEmbedderTopic,
		appState.Config.Extractors.Messages.Embeddings.Enabled,
		func() models.Task { return NewMessageEmbedderTask(appState) },
	)

	addTask(
		ctx,
		string(models.MessageNerTopic),
		models.MessageNerTopic,
		appState.Config.Extractors.Messages.Entities.Enabled,
		func() models.Task { return NewMessageNERTask(appState) },
	)

	addTask(
		ctx,
		string(models.MessageIntentTopic),
		models.MessageIntentTopic,
		appState.Config.Extractors.Messages.Intent.Enabled,
		func() models.Task { return NewMessageIntentTask(appState) },
	)

	addTask(
		ctx,
		string(models.MessageTokenCountTopic),
		models.MessageTokenCountTopic,
		true, // Always enabled
		func() models.Task { return NewMessageTokenCountTask(appState) },
	)

	addTask(
		ctx,
		string(models.DocumentEmbedderTopic),
		models.DocumentEmbedderTopic,
		appState.Config.Extractors.Documents.Embeddings.Enabled,
		func() models.Task { return NewDocumentEmbedderTask(appState) },
	)

	addTask(
		ctx,
		string(models.MessageSummaryEmbedderTopic),
		models.MessageSummaryEmbedderTopic,
		appState.Config.Extractors.Messages.Summarizer.Embeddings.Enabled,
		func() models.Task { return NewMessageSummaryEmbedderTask(appState) },
	)

	addTask(
		ctx,
		string(models.MessageSummaryNERTopic),
		models.MessageSummaryNERTopic,
		appState.Config.Extractors.Messages.Summarizer.Entities.Enabled,
		func() models.Task { return NewMessageSummaryNERTask(appState) },
	)

}
