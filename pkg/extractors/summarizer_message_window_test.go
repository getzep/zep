package extractors

import (
	"context"
	"fmt"
	"github.com/danielchalef/zep/internal"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/memorystore"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/redis/go-redis/v9"
	"github.com/spf13/viper"
	"github.com/stretchr/testify/assert"
	"sync"
	"testing"
)

// Deduplicate this test setup with the redis unit/integration tests
func createRedisClient() *redis.Client {
	redisURL := viper.GetString("datastore.url")
	if redisURL == "" {
		log.Fatal("datastore.url is not set")
	}
	return redis.NewClient(&redis.Options{
		Addr: redisURL,
	})
}

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

func TestProcessOverLimitMessages(t *testing.T) {
	internal.SetDefaultsAndEnv()
	appState := models.AppState{OpenAIClient: llms.CreateOpenAIClient()}

	tests := []struct {
		name                     string
		overLimitMessages        []models.Message
		summary                  string
		summarizerMaxInputTokens int
		expectedSummary          string
		expectedErrorSubstring   string
	}{
		{
			name:                     "Summarize 5 messages",
			overLimitMessages:        internal.MessagesSummary.Messages[5:10],
			summary:                  "A conversation where the user and assistant discuss planning a trip to Iceland. The user asks what the best time to visit is.",
			summarizerMaxInputTokens: 3000,
			expectedErrorSubstring:   "",
		},
		{
			name:                     "Summarize With Limited summarizerMaxInputTokens and Large Message Count",
			overLimitMessages:        internal.MessagesSummary.Messages[5:],
			summary:                  "A conversation where the user and assistant discuss planning a trip to Iceland. The user asks what the best time to visit is.",
			summarizerMaxInputTokens: 100,
			expectedErrorSubstring:   "",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			ctx := context.Background()

			summary, err := processOverLimitMessages(
				ctx,
				&appState,
				test.overLimitMessages,
				test.summarizerMaxInputTokens,
				test.summary,
			)
			fmt.Println("Summary: ", summary)
			assert.NoError(t, err)
			assert.NotEmpty(t, summary)
		})
	}
}

func TestSummaryToMaxMessageWindowSize(t *testing.T) {
	internal.SetDefaultsAndEnv()
	appState := models.AppState{OpenAIClient: llms.CreateOpenAIClient()}

	testMessageCount := len(internal.MessagesSummary.Messages)
	tests := []struct {
		name                   string
		windowSize             int
		messages               []models.Message
		summary                models.Summary
		promptTokens           int
		expectedMessages       []models.Message
		expectedMessageCount   int
		expectedSummary        models.Summary
		expectedErrorSubstring string
	}{
		{
			name:                   "WindowGreaterThanMessages",
			windowSize:             10,
			messages:               internal.MessagesSummary.Messages[:5],
			summary:                models.Summary{Content: "Existing Summary"},
			promptTokens:           -1,
			expectedMessages:       internal.MessagesSummary.Messages[:5],
			expectedMessageCount:   5,
			expectedSummary:        models.Summary{Content: "Existing Summary"},
			expectedErrorSubstring: "",
		},
		{
			name:                   "PrunedToWindowAndNoInitialSummary",
			windowSize:             8,
			messages:               internal.MessagesSummary.Messages,
			summary:                models.Summary{Content: ""},
			promptTokens:           -1,
			expectedMessages:       internal.MessagesSummary.Messages[testMessageCount-4 : testMessageCount],
			expectedMessageCount:   4,
			expectedSummary:        models.Summary{Content: "Some Summary"},
			expectedErrorSubstring: "",
		},
		{
			name:       "PrunedToWindowAndWithInitialSummary",
			windowSize: 8,
			messages:   internal.MessagesSummary.Messages,
			summary: models.Summary{
				Content: "A conversation where the user and assistant discuss planning a trip to Iceland. The user asks what the best time to visit is.",
			},
			promptTokens:           -1,
			expectedMessages:       internal.MessagesSummary.Messages[testMessageCount-4 : testMessageCount],
			expectedMessageCount:   4,
			expectedSummary:        models.Summary{Content: "Some Summary"},
			expectedErrorSubstring: "",
		},
		// Add more test cases here as needed.
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			ctx := context.Background()

			messages, newMessageCount, summary, err := summarizeToMaxMessageWindowSize(
				ctx,
				&appState,
				test.windowSize,
				&test.messages,
				&test.summary,
				test.promptTokens,
			)
			fmt.Println("Summary: ", summary.Content)
			fmt.Println("Messages: ", messages)
			assert.Equal(t, test.expectedMessageCount, newMessageCount)
			assert.Equal(t, test.expectedMessages, *messages)
			if test.summary.Content != "" {
				assert.NotEmpty(t, summary)
			}
			if test.expectedErrorSubstring == "" {
				assert.Nil(t, err)
			} else {
				assert.NotNil(t, err)
				assert.Contains(t, err.Error(), test.expectedErrorSubstring)
			}
		})
	}
}

func TestSummaryWindowExtractorRedis(t *testing.T) {
	internal.SetDefaultsAndEnv()
	appState := createTestAppState()
	// don't limit global session length
	appState.MaxSessionLength = -1
	// Turn off embedding
	appState.Embeddings.Enabled = false
	appState.OpenAIClient = llms.CreateOpenAIClient()
	redisClient := createTestRedisClient(t)
	memoryStore, err := memorystore.NewRedisMemoryStore(appState, redisClient)
	assert.NoError(t, err)
	appState.MemoryStore = memoryStore

	ctx := context.Background()

	// explicitly set the max window size
	viper.Set("memory.message_window", 10)
	expectedMessageCount := 5
	testMessageCount := len(internal.MessagesSummary.Messages)

	sessionID, err := internal.GenerateRandomSessionID(18)
	assert.NoError(t, err)

	wg := sync.WaitGroup{}
	// Add messages to the store.
	err = memoryStore.PutMemory(ctx, appState, sessionID, &internal.MessagesSummary, &wg)
	assert.NoError(t, err)
	wg.Wait()

	maxMsgWindowExtractor := NewMaxMessageWindowSummaryExtractor(appState)

	err = maxMsgWindowExtractor.Extract(
		ctx,
		appState,
		&models.MessageEvent{SessionID: sessionID},
	)
	assert.NoError(t, err)

	// Get the messages from the store.
	messages, err := memoryStore.GetMemory(ctx, appState, sessionID, 0, 0)
	assert.NoError(t, err)
	assert.Equal(t, expectedMessageCount, len(messages.Messages))
	assert.Equal(
		t,
		internal.MessagesSummary.Messages[testMessageCount-expectedMessageCount:],
		messages.Messages,
	)
	assert.NotEmpty(t, messages.Summary.Content)
}
