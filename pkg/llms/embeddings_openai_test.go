package llms

import (
	"context"
	"testing"
	"time"

	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/sashabaranov/go-openai"
	"github.com/spf13/viper"
	"github.com/stretchr/testify/assert"
)

func TestEmbedMessages(t *testing.T) {
	internal.SetDefaultsAndEnv()
	// Skipping the test if OpenAI API token is not provided
	openAIKey := viper.GetString("OPENAI_API_KEY")
	if openAIKey == "" {
		t.Skip("Skipping test due to missing OpenAI API token")
	}

	var vectorLength int64 = 1536

	// Configure AppState
	appState := &models.AppState{
		Embeddings: &models.EmbeddingsConfig{
			Model:      "AdaEmbeddingV2",
			Dimensions: vectorLength,
			Enabled:    true,
		},
		OpenAIClient: openai.NewClient(openAIKey),
	}

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
