package extractors

import (
	"context"
	"testing"

	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/memorystore"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/danielchalef/zep/test"
	"github.com/spf13/viper"
	"github.com/stretchr/testify/assert"
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

	sessionID, err := test.GenerateRandomSessionID(16)
	assert.NoError(t, err)

	testMessages := test.TestMessages[:5]

	// Add new messages using appState.MemoryStore.PutMemory
	err = store.PutMemory(ctx, appState, sessionID, &models.Memory{Messages: testMessages})
	assert.NoError(t, err)

	// Get messages that are missing embeddings using appState.MemoryStore.GetMessageVectors
	unembeddedMessages, err := store.GetMessageVectors(ctx, appState, sessionID, false)
	assert.NoError(t, err)
	assert.True(t, len(unembeddedMessages) == len(testMessages))

	expectedMessages := make([]models.Message, len(testMessages))
	for i, m := range testMessages {
		expectedMessages[i] = m
		expectedMessages[i].UUID = unembeddedMessages[i].TextUUID
	}

	// Create messageEvent with sample data
	messageEvent := &models.MessageEvent{
		SessionID: sessionID,
		Messages:  expectedMessages,
	}

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
