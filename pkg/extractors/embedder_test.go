package extractors

import (
	"context"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/memorystore"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/danielchalef/zep/test"
	"github.com/google/uuid"
	"github.com/spf13/viper"
	"github.com/stretchr/testify/assert"
	"testing"
	"time"
)

func TestEmbeddingExtractor_Extract(t *testing.T) {
	ctx := context.Background()

	// Force embedding to be enabled
	viper.Set("extractor.embeddings.enabled", true)

	db := memorystore.NewPostgresConn(test.TestDsn)
	memorystore.CleanDB(t, db)

	cfg, err := test.NewTestConfig()
	assert.NoError(t, err)

	appState := &models.AppState{Config: cfg}
	store, err := memorystore.NewPostgresMemoryStore(appState, db)
	assert.NoError(t, err)
	appState.MemoryStore = store
	appState.OpenAIClient = llms.CreateOpenAIClient(cfg)

	// we use a vector initialized with all 0.0 as the nil value
	// for the vectorstore records
	nilVector := make([]float32, 1536)

	// Create messageEvent with sample data
	messageEvent := &models.MessageEvent{
		SessionID: "test_session",
		Messages: []models.Message{
			{
				UUID:       uuid.New(),
				CreatedAt:  time.Now(),
				Role:       "user",
				Content:    "Test message",
				TokenCount: 3,
			},
		},
	}

	memoryMessages := &models.Memory{
		Messages: messageEvent.Messages,
	}

	// Add new messages using appState.MemoryStore.PutMemory
	err = store.PutMemory(ctx, appState, messageEvent.SessionID, memoryMessages)
	assert.NoError(t, err)

	embeddingExtractor := NewEmbeddingExtractor()
	err = embeddingExtractor.Extract(ctx, appState, messageEvent)
	assert.NoError(t, err)

	embeddedMessages, err := store.GetMessageVectors(
		ctx,
		appState,
		messageEvent.SessionID,
		true,
	)
	assert.NoError(t, err)

	// Test if the length of embeddedMessages is equal to the length of messageEvent.Messages
	assert.Equal(t, len(messageEvent.Messages), len(embeddedMessages))

	// Test if embeddedMessages have embeddings after running EmbeddingExtractor.Extract
	for _, embeddedMessage := range embeddedMessages {
		assert.NotEqual(
			t,
			nilVector,
			embeddedMessage.Embedding,
			"embeddings should not be the nilVector",
		)
	}
}
