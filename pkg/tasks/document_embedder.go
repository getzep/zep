package tasks

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
)

var _ models.Task = &DocumentEmbedderTask{}

type DocumentEmbedderTask struct {
	appState *models.AppState
}

func (dt *DocumentEmbedderTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	ctx, done := context.WithTimeout(ctx, TaskTimeout*time.Second)
	defer done()

	collectionName := msg.Metadata.Get("collection_name")
	if collectionName == "" {
		return fmt.Errorf("DocumentEmbedderTask collection_name is empty")
	}
	log.Debugf("DocumentEmbedderTask called for collection %s", collectionName)

	var t []models.DocEmbeddingTask
	err := json.Unmarshal(msg.Payload, &t)
	if err != nil {
		return err
	}

	err = dt.Process(ctx, collectionName, t)
	if err != nil {
		return err
	}

	msg.Ack()

	return nil
}

func (dt *DocumentEmbedderTask) Process(
	ctx context.Context,
	collectionName string,
	docTasks []models.DocEmbeddingTask,
) error {
	docType := "document"

	texts := make([]string, len(docTasks))
	for i, r := range docTasks {
		texts[i] = r.Content
	}

	model, err := llms.GetEmbeddingModel(dt.appState, docType)
	if err != nil {
		return fmt.Errorf("DocumentEmbedderTask get embedding model failed: %w", err)
	}

	embeddings, err := llms.EmbedTexts(ctx, dt.appState, model, docType, texts)
	if err != nil {
		return fmt.Errorf("DocumentEmbedderTask embed failed: %w", err)
	}

	docs := make([]models.Document, len(docTasks))
	for i := range docTasks {
		d := models.Document{
			DocumentBase: models.DocumentBase{
				UUID:       docTasks[i].UUID,
				IsEmbedded: true,
			},
			Embedding: embeddings[i],
		}
		docs[i] = d
	}
	err = dt.appState.DocumentStore.UpdateDocuments(
		ctx,
		collectionName,
		docs,
	)
	if err != nil {
		if errors.Is(err, models.ErrNotFound) {
			log.Warnf("DocumentEmbedderTask UpdateDocuments not found. Were the records deleted? %v", err)
			// Don't error out
			return nil
		}
		return fmt.Errorf("DocumentEmbedderTask save embeddings failed: %w", err)
	}
	return nil
}

func (dt *DocumentEmbedderTask) HandleError(err error) {
	log.Errorf("DocumentEmbedderTask error: %s", err)
}
