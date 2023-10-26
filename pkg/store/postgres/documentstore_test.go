package postgres

import (
	"testing"

	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
)

func TestChunkTasks(t *testing.T) {
	tasks := []models.DocEmbeddingTask{
		{UUID: uuid.New()},
		{UUID: uuid.New()},
		{UUID: uuid.New()},
		{UUID: uuid.New()},
	}

	chunks := chunkTasks(tasks, 2)

	assert.Equal(t, 2, len(chunks))
	assert.Equal(t, 2, len(chunks[0]))
	assert.Equal(t, 2, len(chunks[1]))
}
