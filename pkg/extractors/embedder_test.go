package extractors

import (
	"context"
	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/memorystore"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/spf13/viper"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"github.com/uptrace/bun"
	"testing"
	"time"
)

func TestEmbeddingExtractor_Extract(t *testing.T) {
	ctx := context.Background()

	internal.SetDefaultsAndEnv()

	// Force embedding to be enabled
	viper.Set("extractor.embeddings.enabled", true)

	// Initialize appState
	appState := &models.AppState{}
	db := memorystore.NewPostgresConn(dsn)
	cleanDB(t, db)

	store, err := memorystore.NewPostgresMemoryStore(appState, db)
	assert.NoError(t, err)
	appState.MemoryStore = store
	appState.OpenAIClient = llms.CreateOpenAIClient()
	appState.Embeddings = &models.EmbeddingsConfig{
		Model:      "AdaEmbeddingV2",
		Dimensions: 1536,
		Enabled:    true,
	}

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
	err = appState.MemoryStore.PutMemory(ctx, appState, messageEvent.SessionID, memoryMessages)
	assert.NoError(t, err)

	embeddingExtractor := NewEmbeddingExtractor()
	err = embeddingExtractor.Extract(ctx, appState, messageEvent)
	assert.NoError(t, err)

	embeddedMessages, err := appState.MemoryStore.GetMessageVectors(
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

func cleanDB(t *testing.T, db *bun.DB) {
	_, err := db.NewDropTable().
		Model(&memorystore.PgSession{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)

	_, err = db.NewDropTable().
		Model(&memorystore.PgMessageStore{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)
	_, err = db.NewDropTable().
		Model(&memorystore.PgMessageVectorStore{}).
		IfExists().
		Cascade().
		Exec(context.Background())
	require.NoError(t, err)
	_, err = db.NewDropTable().
		Model(&memorystore.PgSummaryStore{}).
		Cascade().
		IfExists().
		Exec(context.Background())
	require.NoError(t, err)
}
