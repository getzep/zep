package postgres

import (
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
	"testing"
)

func TestMemoryDAO_Create(t *testing.T) {
	messages := []models.Message{
		{
			Role:     "user",
			Content:  "Hello",
			Metadata: map[string]interface{}{"timestamp": "1629462540"},
		},
		{
			Role:     "bot",
			Content:  "Hi there!",
			Metadata: map[string]interface{}{"key": "value"},
		},
	}

	memory := &models.Memory{
		Messages: messages,
	}

	t.Run("Create with existing Session", func(t *testing.T) {
		sessionID := createSession(t)
		memoryDAO, err := NewMemoryDAO(testDB, appState, sessionID)
		assert.NoError(t, err, "NewMemoryDAO should not return an error")

		err = memoryDAO.Create(testCtx, memory, true)
		assert.NoError(t, err, "Create should not return an error")

		resultMemory, err := memoryDAO.Get(testCtx, 0)
		assert.NoError(t, err, "Get should not return an error")

		for i := range memory.Messages {
			assert.Equal(t, memory.Messages[i].Role, resultMemory.Messages[i].Role)
			assert.Equal(t, memory.Messages[i].Content, resultMemory.Messages[i].Content)
			assert.Equal(t, memory.Messages[i].Metadata, resultMemory.Messages[i].Metadata)
		}
	})

	t.Run("Create with new Session", func(t *testing.T) {
		sessionID := testutils.GenerateRandomString(16)
		memoryDAO, err := NewMemoryDAO(testDB, appState, sessionID)
		assert.NoError(t, err, "NewMemoryDAO should not return an error")

		err = memoryDAO.Create(testCtx, memory, true)
		assert.NoError(t, err, "Create should not return an error")

		resultMemory, err := memoryDAO.Get(testCtx, 0)
		assert.NoError(t, err, "Get should not return an error")

		for i := range memory.Messages {
			assert.Equal(t, memory.Messages[i].Role, resultMemory.Messages[i].Role)
			assert.Equal(t, memory.Messages[i].Content, resultMemory.Messages[i].Content)
			assert.Equal(t, memory.Messages[i].Metadata, resultMemory.Messages[i].Metadata)
		}
	})

	t.Run(
		"Create memory with deleted session should not error",
		func(t *testing.T) {
			sessionID := createSession(t)
			memoryDAO, err := NewMemoryDAO(testDB, appState, sessionID)
			assert.NoError(t, err, "NewMemoryDAO should not return an error")

			err = memoryDAO.Create(testCtx, memory, true)
			assert.NoError(t, err, "Create should not return an error")

			sessionStore := NewSessionDAO(testDB)
			err = sessionStore.Delete(testCtx, sessionID)
			assert.NoError(t, err, "deleteSession should not return an error")

			err = memoryDAO.Create(testCtx, memory, true)
			assert.NoError(t, err, "Create for deleted session should not return an error")
		},
	)
}

func TestMemoryDAO_Get(t *testing.T) {
	// Create a test session
	sessionID := createSession(t)

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err, "NewMessageDAO should not return an error")

	// Create a bunch of messages
	messages, err := messageDAO.CreateMany(testCtx, testutils.TestMessages)
	assert.NoError(t, err, "CreateMany should not return an error")

	expectedMessages := make([]models.Message, len(testutils.TestMessages))
	copy(expectedMessages, messages)

	// messageWindow in test defaults to 12
	messageWindow := appState.Config.Memory.MessageWindow
	// Get the index for the last message in the summary
	summaryPointIndex := len(messages) - messageWindow/2 - 1

	tests := []struct {
		name           string
		sessionID      string
		lastNMessages  int
		expectedLength int
		withSummary    bool
	}{
		{
			name:           "Get all messages within messageWindow",
			sessionID:      sessionID,
			lastNMessages:  0,
			expectedLength: messageWindow,
			withSummary:    false,
		},
		{
			name:           "Get all messages up to SummaryPoint",
			sessionID:      sessionID,
			lastNMessages:  0,
			expectedLength: len(expectedMessages) - summaryPointIndex - 1,
			withSummary:    true,
		},
		{
			name:           "Get last message",
			sessionID:      sessionID,
			lastNMessages:  2,
			expectedLength: 2,
			withSummary:    false,
		},
		{
			name:           "Non-existent session",
			sessionID:      "nonexistent",
			lastNMessages:  0,
			expectedLength: 0,
			withSummary:    false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			memoryDAO, err := NewMemoryDAO(testDB, appState, tt.sessionID)
			assert.NoError(t, err, "NewMemoryDAO should not return an error")

			if tt.withSummary {
				summaryDAO, err := NewSummaryDAO(testDB, appState, sessionID)
				assert.NoError(t, err, "NewSummaryDAO should not return an error")

				// Create a summary using the test messages. The SummaryPointUUID should be at messageWindow - 2
				_, err = summaryDAO.Create(
					testCtx,
					&models.Summary{Content: "Test summary",
						SummaryPointUUID: messages[summaryPointIndex].UUID},
				)
				assert.NoError(t, err)

			}

			switch {
			case tt.expectedLength == 0:
				expectedMessages = []models.Message{}
			case tt.withSummary:
				expectedMessages = expectedMessages[len(expectedMessages)-(messageWindow/2):]
			case tt.lastNMessages == 0:
				expectedMessages = expectedMessages[len(expectedMessages)-messageWindow:]
			case tt.lastNMessages > 0:
				expectedMessages = expectedMessages[len(expectedMessages)-tt.lastNMessages:]
			default:
				expectedMessages = []models.Message{}
			}

			result, err := memoryDAO.Get(
				testCtx,
				tt.lastNMessages,
			)
			assert.NoError(t, err)

			if tt.expectedLength > 0 {
				assert.NotNil(t, result)
				assert.Equal(t, tt.expectedLength, len(result.Messages))
				for i, msg := range result.Messages {
					expectedMessage := expectedMessages[i]
					assert.NotEmpty(t, msg.UUID)
					assert.False(t, msg.CreatedAt.IsZero())
					assert.Equal(t, expectedMessage.Role, msg.Role)
					assert.Equal(
						t,
						expectedMessage.Content,
						msg.Content,
					)
					assert.True(
						t,
						equivalentMaps(expectedMessage.Metadata, msg.Metadata),
					)
				}
			} else {
				assert.Empty(t, result.Summary)
				assert.Empty(t, result.Messages)
			}
		})
	}
}
