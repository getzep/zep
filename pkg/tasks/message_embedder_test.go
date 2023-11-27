package tasks

import (
	"math"
	"testing"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func TestEmbeddingExtractor_Extract_OpenAI(t *testing.T) {
	llmClient, err := llms.NewOpenAILLM(testCtx, appState.Config)
	assert.NoError(t, err)
	appState.LLMClient = llmClient

	store := appState.MemoryStore

	documentType := "message"

	sessionID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err)

	testMessages := testutils.TestMessages[:5]

	// Add new messages using appState.MemoryStore.PutMemory
	err = store.PutMemory(
		testCtx,
		sessionID,
		&models.Memory{Messages: testMessages},
		true,
	)
	assert.NoError(t, err)

	// Get messages that are missing embeddings using appState.MemoryStore.GetMessageEmbeddings
	memories, err := store.GetMemory(testCtx, sessionID, 0)
	assert.NoError(t, err)
	assert.True(t, len(memories.Messages) == len(testMessages))

	unembeddedMessages := memories.Messages

	texts := make([]string, len(unembeddedMessages))
	for i, r := range unembeddedMessages {
		texts[i] = r.Content
	}

	model := &models.EmbeddingModel{
		Service:    "openai",
		Dimensions: 1536,
	}
	embeddings, err := llms.EmbedTexts(testCtx, appState, model, documentType, texts)
	assert.NoError(t, err)

	expectedEmbeddingRecords := make([]models.TextData, len(unembeddedMessages))
	for i, r := range unembeddedMessages {
		expectedEmbeddingRecords[i] = models.TextData{
			TextUUID:  r.UUID,
			Text:      r.Content,
			Embedding: embeddings[i],
		}
	}

	task := NewMessageEmbedderTask(appState)
	err = task.Process(testCtx, sessionID, unembeddedMessages)
	assert.NoError(t, err)

	embeddedMessages, err := store.GetMessageEmbeddings(
		testCtx,
		sessionID,
	)
	assert.NoError(t, err)

	assert.Equal(t, len(expectedEmbeddingRecords), len(embeddedMessages))

	expectedEmbeddingRecordsMap := make(map[string]models.TextData)
	for _, r := range expectedEmbeddingRecords {
		expectedEmbeddingRecordsMap[r.TextUUID.String()] = r
	}

	embeddedMessagesMap := make(map[string]models.TextData)
	for _, r := range embeddedMessages {
		embeddedMessagesMap[r.TextUUID.String()] = r
	}

	assert.Equal(t, len(expectedEmbeddingRecordsMap), len(embeddedMessagesMap))

	for uuid, expectedRecord := range expectedEmbeddingRecordsMap {
		actualRecord, ok := embeddedMessagesMap[uuid]
		assert.True(t, ok)

		assert.Equal(t, expectedRecord.TextUUID, actualRecord.TextUUID)
		assert.Equal(t, expectedRecord.Text, actualRecord.Text)
		compareFloat32Vectors(t, expectedRecord.Embedding, actualRecord.Embedding, 0.001)
	}
}

// compareFloat32Vectors compares two float32 vectors, asserting that their values are within the given variance.
func compareFloat32Vectors(t *testing.T, a, b []float32, variance float32) {
	t.Helper()

	if len(a) != len(b) {
		t.Fatalf("Vectors have different lengths: len(a)=%d, len(b)=%d", len(a), len(b))
	}

	for i := 0; i < len(a); i++ {
		diff := float32(math.Abs(float64(a[i] - b[i])))
		if diff > variance {
			t.Fatalf(
				"Vectors differ at index %d: a=%v, b=%v, diff=%v, variance=%v",
				i,
				a[i],
				b[i],
				diff,
				variance,
			)
		}
	}
}
