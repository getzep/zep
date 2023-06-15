package llms

import (
	"context"
	"testing"
	"time"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
)

func TestEmbedOpenAI(t *testing.T) {
	cfg := testutils.NewTestConfig()

	appState := &models.AppState{Config: cfg}
	appState.OpenAIClient = NewOpenAIRetryClient(cfg)

	vectorLength := 1536

	messageContents := []string{"Text 1", "Text 2"}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	embeddings, err := EmbedTextsOpenAI(ctx, appState, messageContents)
	assert.NoError(t, err)
	assert.NotNil(t, embeddings)
	assert.Len(t, embeddings, 2)

	// Check if the embeddings are of the correct length
	for _, embedding := range embeddings {
		assert.Len(t, embedding, vectorLength)
	}
}
