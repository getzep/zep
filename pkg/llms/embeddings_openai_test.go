package llms

import (
	"context"
	"testing"
	"time"

	"github.com/getzep/zep/test"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
)

func TestEmbedMessages(t *testing.T) {
	cfg := test.NewTestConfig()

	appState := &models.AppState{Config: cfg}
	appState.OpenAIClient = CreateOpenAIClient(cfg)

	vectorLength := 1536

	messageContents := []string{"Text 1", "Text 2"}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	embeddings, err := EmbedMessages(ctx, appState, messageContents)
	assert.NoError(t, err)
	assert.NotNil(t, embeddings)
	assert.Len(t, *embeddings, 2)

	// Check if the embeddings are of the correct length
	for _, embedding := range *embeddings {
		assert.Len(t, embedding.Embedding, int(vectorLength))
	}
}
