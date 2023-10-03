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

const DefaultTaskChunkSize = 1000
const DefaultPoolSize = 2
const DefaultPoolBuffer = 1000

// NewDocEmbeddingProcessor creates a new DocEmbeddingProcessor
func NewDocEmbeddingProcessor(
	appState *models.AppState,
	embeddingTaskCh chan []models.DocEmbeddingTask,
	embeddingUpdateCh chan []models.DocEmbeddingUpdate,
) *DocEmbeddingProcessor {
	maxProcs := DefaultPoolSize
	if appState.Config.Extractors.Documents.Embeddings.MaxProcs > 0 {
		maxProcs = appState.Config.Extractors.Documents.Embeddings.MaxProcs
	}
	taskChunkSize := DefaultTaskChunkSize
	if appState.Config.Extractors.Documents.Embeddings.ChunkSize > 0 {
		taskChunkSize = appState.Config.Extractors.Documents.Embeddings.ChunkSize
	}
	poolBuffer := DefaultPoolBuffer
	if appState.Config.Extractors.Documents.Embeddings.BufferSize > 0 {
		poolBuffer = appState.Config.Extractors.Documents.Embeddings.BufferSize
	}

	return &DocEmbeddingProcessor{
		appState:          appState,
		EmbeddingTaskCh:   embeddingTaskCh,
		EmbeddingUpdateCh: embeddingUpdateCh,
		PoolSize:          maxProcs,
		PoolBuffer:        poolBuffer,
		ChunkSize:         taskChunkSize,
	}
}

// DocEmbeddingProcessor is a processor for embedding documents
type DocEmbeddingProcessor struct {
	appState          *models.AppState
	model             *models.EmbeddingModel
	documentType      string
	EmbeddingTaskCh   chan []models.DocEmbeddingTask
	EmbeddingUpdateCh chan<- []models.DocEmbeddingUpdate
	Pool              *pond.WorkerPool
	PoolSize          int
	PoolBuffer        int
	ChunkSize         int
	once              sync.Once
}

// Run starts the DocEmbeddingProcessor. It is safe to call this multiple times.
// It will only start the processor once.
func (ep *DocEmbeddingProcessor) Run(
	ctx context.Context,
) error {
	ep.documentType = "document"
	model, err := llms.GetEmbeddingModel(ep.appState, ep.documentType)
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

// processor is the main loop for the DocEmbeddingProcessor. It
// receives embedding tasks, Submits them to the pool, embeds the documents,
// and sends the updates to the document store.
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
			taskChunks := chunkTasks(tasks, ep.ChunkSize)
			for _, taskChunk := range taskChunks {
				// Capture range variable
				taskChunk := taskChunk
				ep.Pool.Submit(func() {
					updates, err := ep.processEmbeddingTasks(ctx, taskChunk)
					if err != nil {
						log.Errorf("failed to process embedding tasks: %s", err)
						return
					}
					ep.EmbeddingUpdateCh <- updates
				})
			}
		}
	}
}

// processEmbeddingTasks embeds the documents in the given tasks and returns
// the updates to processor.
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

// chunkTasks splits the given tasks into chunks of the given size.
func chunkTasks(tasks []models.DocEmbeddingTask, chunkSize int) [][]models.DocEmbeddingTask {
	var chunks [][]models.DocEmbeddingTask
	for i := 0; i < len(tasks); i += chunkSize {
		end := i + chunkSize
		if end > len(tasks) {
			end = len(tasks)
		}
		chunks = append(chunks, tasks[i:end])
	}
	return chunks
}
