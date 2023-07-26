package extractors

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/getzep/zep/pkg/llms"

	"github.com/google/uuid"

	"github.com/alitto/pond"
	"github.com/getzep/zep/pkg/models"
)

// TODO move pool size and buffer to config
func NewDocEmbeddingProcessor(
	appState *models.AppState,
	embeddingTaskCh chan []models.DocEmbeddingTask,
	embeddingUpdateCh chan []models.DocEmbeddingUpdate,
) *DocEmbeddingProcessor {
	return &DocEmbeddingProcessor{
		appState:          appState,
		EmbeddingTaskCh:   embeddingTaskCh,
		EmbeddingUpdateCh: embeddingUpdateCh,
		PoolSize:          1,
		PoolBuffer:        100,
	}
}

type DocEmbeddingProcessor struct {
	appState          *models.AppState
	model             *models.EmbeddingModel
	documentType      string
	EmbeddingTaskCh   chan []models.DocEmbeddingTask
	EmbeddingUpdateCh chan<- []models.DocEmbeddingUpdate
	Pool              *pond.WorkerPool
	PoolSize          int
	PoolBuffer        int
	once              sync.Once
}

func (ep *DocEmbeddingProcessor) Run(
	ctx context.Context,
) error {
	ep.documentType = "document"
	model, err := llms.GetMessageEmbeddingModel(ep.appState, ep.documentType)
	if err != nil {
		return fmt.Errorf("failed to get embedding model: %w", err)
	}
	ep.model = model

	ep.once.Do(func() {
		go func() {
			ep.processor(ctx)
		}()
	})
	log.Info("started document embedding processor")

	return nil
}

func (ep *DocEmbeddingProcessor) processor(ctx context.Context) {
	pool := pond.New(ep.PoolSize, ep.PoolBuffer)
	defer pool.StopAndWait()
	defer close(ep.EmbeddingTaskCh)

	ep.Pool = pool

	for {
		select {
		case <-ctx.Done():
			return
		case tasks := <-ep.EmbeddingTaskCh:
			ep.Pool.Submit(func() {
				updates, err := ep.processEmbeddingTasks(ctx, tasks)
				if err != nil {
					log.Errorf("failed to process embedding tasks: %s", err)
					return
				}
				ep.EmbeddingUpdateCh <- updates
			})
		}
	}
}

func (ep *DocEmbeddingProcessor) processEmbeddingTasks(
	ctx context.Context,
	tasks []models.DocEmbeddingTask,
) ([]models.DocEmbeddingUpdate, error) {
	if len(tasks) == 0 {
		return nil, nil
	}

	uuids := make([]uuid.UUID, len(tasks))
	texts := make([]string, len(tasks))
	for i := range tasks {
		uuids[i] = tasks[i].UUID
		texts[i] = tasks[i].Content
	}

	embeddings, err := llms.EmbedTexts(ctx, ep.appState, ep.model, ep.documentType, texts)
	if err != nil {
		return nil, fmt.Errorf("failed to embed documents: %w", err)
	}
	if len(embeddings) != len(tasks) {
		return nil, fmt.Errorf("invalid number of embeddings returned")
	}

	updates := make([]models.DocEmbeddingUpdate, len(tasks))
	for i := range tasks {
		updates[i] = models.DocEmbeddingUpdate{
			UUID:           uuids[i],
			Embedding:      embeddings[i],
			CollectionName: tasks[i].CollectionName,
			ProcessedAt:    time.Now().UTC(),
		}
	}

	return updates, nil
}
