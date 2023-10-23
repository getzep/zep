package tasks

import (
	"context"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
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
		"message_summarizer",
		"new_messages",
		appState.Config.Extractors.Messages.Summarizer.Enabled,
		func() models.Task { return &MessageSummaryTask{appState: appState} },
	)

	addTask(
		ctx,
		"message_embedder",
		"new_messages",
		appState.Config.Extractors.Messages.Embeddings.Enabled,
		func() models.Task { return &MessageEmbedderTask{appState: appState} },
	)
}
