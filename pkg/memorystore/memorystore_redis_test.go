package memorystore

import (
	"context"
	"fmt"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/stretchr/testify/require"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/redis/go-redis/v9"
	"github.com/sashabaranov/go-openai"
	"github.com/spf13/viper"
	"github.com/stretchr/testify/assert"
)

func createTestRedisClient(t *testing.T) *redis.Client {
	viper.SetDefault("datastore.url", "localhost:6379")
	redisClient := createRedisClient()

	// Ensure Redis is connected and working.
	_, err := redisClient.Ping(context.Background()).Result()
	if err != nil {
		t.Skip("Skipping test due to Redis not available:", err)
	}

	return redisClient
}

func createTestAppState() *models.AppState {
	return &models.AppState{
		SessionLock:      &sync.Map{},
		MaxSessionLength: 10,
		Embeddings: &models.Embeddings{
			Enabled: false,
		},
	}
}

func generateTestMessagesAndSummary() models.MessagesAndSummary {
	return models.MessagesAndSummary{
		Messages: []models.Message{
			{
				Role:    "user",
				Content: "Hello",
			},
			{
				Role:    "assistant",
				Content: "Hi there!",
			},
		},
		Summary: models.Summary{
			Content: "A short conversation where the user says Hello and the assistant replies with Hi there!",
		},
	}
}

func waitForKeys(
	ctx context.Context,
	keyPrefix string,
	targetCount int,
	client *redis.Client,
	timeout time.Duration,
	tickInterval time.Duration,
) error {
	timeoutChannel := time.After(timeout * time.Second)
	tick := time.Tick(tickInterval * time.Millisecond)

	for {
		select {
		case <-timeoutChannel:
			return fmt.Errorf("timed out waiting for Redis keys")
		case <-tick:
			numKeys := int64(0)
			iter := client.Scan(ctx, 0, keyPrefix, 10).Iterator()
			for iter.Next(ctx) {
				numKeys++
			}
			if numKeys == int64(targetCount) {
				return nil
			}
		}
	}
}

func TestPutMemory(t *testing.T) {
	ctx := context.Background()
	internal.SetDefaultsAndEnv()

	appState := createTestAppState()
	redisClient := createTestRedisClient(t)
	rds, err := NewRedisMemoryStore(appState, redisClient)
	require.NoError(t, err)
	sessionID, err := internal.GenerateRandomSessionID(16)
	require.NoError(t, err)

	memoryMessages := generateTestMessagesAndSummary()

	wg := &sync.WaitGroup{}
	err = rds.PutMemory(ctx, appState, sessionID, &memoryMessages, wg)
	assert.NoError(t, err)

	// Check that the messages were stored correctly
	// They'll be in reverse order because we're using LPush
	storedMessages, err := redisClient.LRange(ctx, sessionID, 0, -1).Result()
	assert.NoError(t, err)
	assert.Equal(t, []string{
		"assistant: Hi there!",
		"user: Hello",
	}, storedMessages)

	// Check that the summaries were stored correctly
	summaryKey := sessionID + "_summary"
	storedSummary, err := redisClient.Get(ctx, summaryKey).Result()
	assert.NoError(t, err)
	assert.Equal(t, memoryMessages.Summary.Content, storedSummary)

	wg.Wait()
	redisClient.Close()
}

func TestPutMemoryMaxSessionLength(t *testing.T) {
	ctx := context.Background()
	internal.SetDefaultsAndEnv()

	appState := createTestAppState()
	appState.MaxSessionLength = 2

	redisClient := createTestRedisClient(t)
	rds, err := NewRedisMemoryStore(appState, redisClient)
	require.NoError(t, err)
	sessionID, err := internal.GenerateRandomSessionID(16)
	require.NoError(t, err)

	memoryMessages := models.MessagesAndSummary{
		Messages: []models.Message{
			{
				Role:    "user",
				Content: "Hello",
			},
			{
				Role:    "assistant",
				Content: "Hi there!",
			},
			{
				Role:    "user",
				Content: "How are you?",
			},
			{
				Role:    "assistant",
				Content: "I'm doing great! How about you?",
			},
		},
		Summary: models.Summary{
			Content: "A conversation where the user and assistant greet each other and ask how they are.",
		},
	}

	wg := &sync.WaitGroup{}
	err = rds.PutMemory(ctx, appState, sessionID, &memoryMessages, wg)
	assert.NoError(t, err)

	// Check that the messages were stored correctly and that the 2 oldest messages were trimmed
	// They'll be in reverse order because we're using LPush
	wg.Wait() // wait until prune has completed
	storedMessages, err := redisClient.LRange(ctx, sessionID, 0, -1).Result()
	assert.NoError(t, err)
	assert.Equal(t, []string{
		"assistant: I'm doing great! How about you?",
		"user: How are you?",
	}, storedMessages)

	// Check that the summaries were stored correctly
	summaryKey := sessionID + "_summary"
	storedSummary, err := redisClient.Get(ctx, summaryKey).Result()
	assert.NoError(t, err)
	assert.Equal(t, memoryMessages.Summary.Content, storedSummary)

	wg.Wait()
	redisClient.Close()
}

func TestGetMemory(t *testing.T) {
	ctx := context.Background()
	internal.SetDefaultsAndEnv()

	appState := createTestAppState()
	redisClient := createTestRedisClient(t)
	rds, err := NewRedisMemoryStore(appState, redisClient)
	require.NoError(t, err)
	sessionID, err := internal.GenerateRandomSessionID(16)
	require.NoError(t, err)

	putMemoryMessages := models.MessagesAndSummary{
		Messages: []models.Message{
			{
				Role:    "user",
				Content: "Hello",
			},
			{
				Role:    "assistant",
				Content: "Hi there!",
			},
			{
				Role:    "user",
				Content: "How are you?",
			},
			{
				Role:    "assistant",
				Content: "I'm doing great! How about you?",
			},
		},
		Summary: models.Summary{
			Content: "A conversation where the user and assistant greet each other and ask how they are.",
		},
	}

	wg := &sync.WaitGroup{}
	err = rds.PutMemory(ctx, appState, sessionID, &putMemoryMessages, wg)
	assert.NoError(t, err)

	// Test retrieving only the last 2 messages
	getMemoryResponse, err := rds.GetMemory(ctx, appState, sessionID, 2, 0)
	assert.NoError(t, err)

	expectedMessages := putMemoryMessages.Messages[2:]

	expectedSummary := putMemoryMessages.Summary
	expectedTokens := int64(0)

	assert.Equal(t, expectedMessages, getMemoryResponse.Messages)
	assert.Equal(t, expectedSummary, getMemoryResponse.Summary)
	assert.Equal(t, expectedTokens, getMemoryResponse.Tokens)

	// Test retrieving all messages
	getMemoryResponseAll, err := rds.GetMemory(ctx, appState, sessionID, 4, 0)
	assert.NoError(t, err)

	expectedMessagesAll := putMemoryMessages.Messages

	assert.Equal(t, expectedMessagesAll, getMemoryResponseAll.Messages)
	assert.Equal(t, expectedSummary, getMemoryResponseAll.Summary)
	assert.Equal(t, expectedTokens, getMemoryResponseAll.Tokens)

	wg.Wait()
	redisClient.Close()
}

func TestGetSummary(t *testing.T) {
	ctx := context.Background()
	internal.SetDefaultsAndEnv()

	appState := createTestAppState()
	redisClient := createTestRedisClient(t)
	rds, err := NewRedisMemoryStore(appState, redisClient)
	require.NoError(t, err)
	sessionID, err := internal.GenerateRandomSessionID(16)
	require.NoError(t, err)

	_, err = rds.Client.Set(ctx, sessionID+"_summary", "Test summary content.", 0).Result()
	require.NoError(t, err)

	t.Run("Get existing summary", func(t *testing.T) {
		summary, err := rds.GetSummary(ctx, appState, sessionID)
		require.NoError(t, err)
		assert.Equal(t, "Test summary content.", summary.Content)
	})

	t.Run("Get non-existing summary", func(t *testing.T) {
		_, err := rds.GetSummary(ctx, nil, "non_existing_id")
		require.Error(t, err)
		assert.Contains(t, err.Error(), "summary not found")
	})
}

func TestGenerateEmbeddings(t *testing.T) {
	// Initialize Redis client for integration tests.
	ctx := context.Background()

	internal.SetDefaultsAndEnv()

	openAIKey := viper.GetString("OPENAI_API_KEY")
	if openAIKey == "" {
		t.Skip("Skipping test due to missing OpenAI API token")
	}

	appState := &models.AppState{
		MaxSessionLength: 10,
		Embeddings: &models.Embeddings{
			Enabled: false,
			Model:   "AdaEmbeddingV2",
		},
		OpenAIClient: openai.NewClient(openAIKey),
	}

	redisClient := createTestRedisClient(t)

	rds, err := NewRedisMemoryStore(appState, redisClient)
	require.NoError(t, err)

	// Delete an existing Redisearch index.
	_, err = redisClient.Do(ctx, "FT.DROP", "zep").Result()
	require.NoError(t, err)

	// Test creation of the Redisearch index.
	err = ensureRedisearchIndex(redisClient, 1536, "cosine")
	require.NoError(t, err)

	sessionID, err := internal.GenerateRandomSessionID(16)
	require.NoError(t, err)
	messages := &[]models.Message{
		{Content: "Message 1", Role: "user"},
		{Content: "Message 2", Role: "bot"},
	}

	err = rds.GenerateEmbeddings(appState, sessionID, messages)
	require.NoError(t, err)

	err = waitForKeys(ctx, "zep:*", len(*messages), redisClient, 10, 100)
	require.NoError(t, err)

	// Verify each embedding has sessionID, vector, content, and role stored correctly.
	iter := redisClient.Scan(ctx, 0, "zep:*", 10).Iterator()
	for iter.Next(ctx) {
		key := iter.Val()
		hashValues, _ := redisClient.HGetAll(ctx, key).Result()

		assert.Equal(t, sessionID, hashValues["session"])
		assert.NotEmpty(t, hashValues["vector"])
		assert.Contains(t, []string{"Message 1", "Message 2"}, hashValues["content"])
		assert.Contains(t, []string{"user", "bot"}, hashValues["role"])
	}
}

func TestSearchMemory(t *testing.T) {
	ctx := context.Background()

	internal.SetDefaultsAndEnv()

	appState := &models.AppState{
		MaxSessionLength: 20,
		OpenAIClient:     llms.CreateOpenAIClient(),
		SessionLock:      &sync.Map{},
		Embeddings: &models.Embeddings{
			Model:      "AdaEmbeddingV2",
			Dimensions: 1536,
			Enabled:    true,
		},
	}

	redisClient := createTestRedisClient(t)

	rds, err := NewRedisMemoryStore(appState, redisClient)
	require.NoError(t, err)

	// Delete an existing Redisearch index.
	_, err = redisClient.Do(ctx, "FT.DROP", "zep").Result()
	require.NoError(t, err)

	// Test creation of the Redisearch index.
	err = ensureRedisearchIndex(redisClient, 1536, "cosine")
	require.NoError(t, err)

	sessionID, err := internal.GenerateRandomSessionID(16)
	require.NoError(t, err)

	wg := &sync.WaitGroup{}
	err = rds.PutMemory(ctx, appState, sessionID, &internal.MessagesSummary, wg)
	assert.NoError(t, err)

	wg.Wait()

	err = waitForKeys(ctx, "zep:*", len(internal.MessagesSummary.Messages), redisClient, 10, 100)
	require.NoError(t, err)

	// Test Search
	searchResults, err := rds.SearchMemory(
		ctx,
		appState,
		sessionID,
		&models.SearchPayload{Text: "Tell me about Reykjavik"},
	)
	require.NoError(t, err)

	expectedLength := 10
	assert.Equal(t, expectedLength, len(*searchResults))

	for i, result := range *searchResults {
		assert.Contains(
			t,
			[]string{"user", "assistant"},
			result.Role,
			fmt.Sprintf("Expected Role at index %d to be 'user' or 'assistant'", i),
		)
		assert.NotEmpty(
			t,
			result.Content,
			fmt.Sprintf("Expected Content at index %d to not be empty", i),
		)
		assert.Greaterf(
			t,
			result.Dist,
			float64(0),
			fmt.Sprintf("Expected Dist at index %d to be float64 and greater than 0", i),
		)
	}
}

func TestDeleteSession(t *testing.T) {
	ctx := context.Background()

	internal.SetDefaultsAndEnv()

	appState := createTestAppState()

	redisClient := createTestRedisClient(t)
	rds, err := NewRedisMemoryStore(appState, redisClient)
	require.NoError(t, err)

	sessionID, err := internal.GenerateRandomSessionID(16)
	require.NoError(t, err)
	summaryKey := fmt.Sprintf("%s_summary", sessionID)
	tokenCountKey := fmt.Sprintf("%s_tokens", sessionID)

	// Set up some keys to delete
	redisClient.Set(ctx, sessionID, "value", 0).Err()
	redisClient.Set(ctx, summaryKey, "value", 0).Err()
	redisClient.Set(ctx, tokenCountKey, "value", 0).Err()

	// Call DeleteSession
	err = rds.DeleteSession(ctx, sessionID)
	require.NoError(t, err)

	// Check if keys were deleted
	existsSessionID, _ := redisClient.Exists(ctx, sessionID).Result()
	existsSummaryKey, _ := redisClient.Exists(ctx, summaryKey).Result()
	existsTokenCountKey, _ := redisClient.Exists(ctx, tokenCountKey).Result()

	assert.Equal(t, int64(0), existsSessionID)
	assert.Equal(t, int64(0), existsSummaryKey)
	assert.Equal(t, int64(0), existsTokenCountKey)
}

func TestEncode(t *testing.T) {
	testCases := []struct {
		name  string
		input []float32
		want  []byte
	}{
		{
			name:  "empty slice",
			input: []float32{},
			want:  []byte{},
		},
		{
			name:  "single value",
			input: []float32{1.0},
			want: []byte{
				0x00, 0x00, 0x80, 0x3F,
			},
		},
		{
			name:  "multiple values",
			input: []float32{1.0, 2.5, 3.1},
			want: []byte{
				0x00, 0x00, 0x80, 0x3F,
				0x00, 0x00, 0x20, 0x40,
				0x66, 0x66, 0x46, 0x40,
			},
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			got := encode(tc.input)
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("encode(%v) = %v; want %v", tc.input, got, tc.want)
			}
		})
	}
}
