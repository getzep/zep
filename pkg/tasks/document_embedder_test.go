package tasks

import (
	"testing"
	"time"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func TestDocumentEmbedderTask_Process(t *testing.T) {
	collectionName := testutils.GenerateRandomString(10)
	err := appState.DocumentStore.CreateCollection(testCtx, models.DocumentCollection{
		Name:                collectionName,
		IsAutoEmbedded:      false,
		EmbeddingDimensions: appState.Config.Extractors.Documents.Embeddings.Dimensions,
	})
	assert.NoError(t, err)

	fakeEmbedding := make([]float32, appState.Config.Extractors.Documents.Embeddings.Dimensions)

	documents := make([]models.Document, 2)
	for i := range documents {
		documents[i] = models.Document{
			DocumentBase: models.DocumentBase{
				DocumentID: testutils.GenerateRandomString(10),
				Content:    testutils.GenerateRandomString(10),
				Metadata:   map[string]interface{}{"key": testutils.GenerateRandomString(3)},
			},
			Embedding: fakeEmbedding,
		}
	}

	uuids, err := appState.DocumentStore.CreateDocuments(testCtx, collectionName, documents)
	assert.NoError(t, err)

	docTasks := make([]models.DocEmbeddingTask, len(documents))
	for i, uuid := range uuids {
		docTasks[i] = models.DocEmbeddingTask{
			UUID: uuid,
		}
	}

	task := NewDocumentEmbedderTask(appState)
	err = task.Process(testCtx, collectionName, docTasks)
	assert.NoError(t, err)

	// Get collection in loop waiting for documents to be embedded
	var collection models.DocumentCollection
	for i := 0; i < 20; i++ {
		collection, err = appState.DocumentStore.GetCollection(testCtx, collectionName)
		assert.NoError(t, err)
		if collection.DocumentCount == collection.DocumentEmbeddedCount {
			break
		}
		time.Sleep(time.Second)
	}

	documents, err = appState.DocumentStore.GetDocuments(testCtx, collectionName, uuids, nil)
	assert.NoError(t, err)

	for _, doc := range documents {
		assert.NotEqual(t, fakeEmbedding, doc.Embedding)
	}
}
