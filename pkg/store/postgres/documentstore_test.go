package postgres

import (
	"testing"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
)

func TestDocumentsFromEmbeddingUpdates(t *testing.T) {
	uuid1 := uuid.New()
	uuid2 := uuid.New()

	updates := []models.DocEmbeddingUpdate{
		{
			UUID:      uuid1,
			Embedding: []float32{0.1, 0.2, 0.3},
		},
		{
			UUID:      uuid2,
			Embedding: []float32{0.4, 0.5, 0.6},
		},
	}

	expectedDocs := []models.Document{
		{
			DocumentBase: models.DocumentBase{
				UUID:       uuid1,
				IsEmbedded: true,
			},
			Embedding: []float32{0.1, 0.2, 0.3},
		},
		{
			DocumentBase: models.DocumentBase{
				UUID:       uuid2,
				IsEmbedded: true,
			},
			Embedding: []float32{0.4, 0.5, 0.6},
		},
	}

	docs := documentsFromEmbeddingUpdates(updates)

	assert.Equal(t, expectedDocs, docs)
}
