package postgres

import (
	"fmt"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"math/rand"
	"testing"
)

func TestNewMessageDAO(t *testing.T) {
	sessionID := "testSessionID"

	t.Run("NewMessageDAO should return a MessageDAO object", func(t *testing.T) {
		messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
		assert.NoError(t, err)
		assert.NotNil(t, messageDAO)
	})

	t.Run("NewMessageDAO should return an error for empty sessionID", func(t *testing.T) {
		emptySessionID := ""
		messageDAO, err := NewMessageDAO(testDB, appState, emptySessionID)
		assert.Error(t, err)
		assert.Nil(t, messageDAO)
	})
}

func TestCreate(t *testing.T) {
	sessionID := createSession(t)

	message := &models.Message{
		UUID:       uuid.New(),
		Role:       "testRole",
		Content:    "testContent",
		TokenCount: 1,
		Metadata:   map[string]interface{}{"key": "value"},
	}

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)
	createdMessage, err := messageDAO.Create(testCtx, message)
	assert.NoError(t, err)

	assert.NoError(t, err)
	assert.Equal(t, message.UUID, createdMessage.UUID)
	assert.Equal(t, message.Role, createdMessage.Role)
	assert.Equal(t, message.Content, createdMessage.Content)
	assert.Equal(t, message.TokenCount, createdMessage.TokenCount)
	assert.Equal(t, message.Metadata, createdMessage.Metadata)
}

func TestCreateMany(t *testing.T) {
	sessionID := createSession(t)

	// Initialize a slice of Message objects with test data
	messages := []models.Message{
		{
			UUID:       uuid.New(),
			Role:       "testRole1",
			Content:    "testContent1",
			TokenCount: 1,
			Metadata:   map[string]interface{}{"key1": "value1"},
		},
		{
			UUID:       uuid.New(),
			Role:       "testRole2",
			Content:    "testContent2",
			TokenCount: 2,
			Metadata:   map[string]interface{}{"key2": "value2"},
		},
	}

	// Call the CreateMany function
	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)
	createdMessages, err := messageDAO.CreateMany(testCtx, messages)

	// Use the assert package to check that no error is returned
	assert.NoError(t, err)

	// Retrieve the created messages from the database
	for i, originalMessage := range messages {
		// Assert that the created message matches the original Message object
		assert.NoError(t, err)
		assert.Equal(t, originalMessage.UUID, createdMessages[i].UUID)
		assert.Equal(t, originalMessage.Role, createdMessages[i].Role)
		assert.Equal(t, originalMessage.Content, createdMessages[i].Content)
		assert.Equal(t, originalMessage.TokenCount, createdMessages[i].TokenCount)
		assert.Equal(t, originalMessage.Metadata, createdMessages[i].Metadata)
	}
}

func TestGet(t *testing.T) {
	sessionID := createSession(t)

	// Initialize a Message object with test data
	message := &models.Message{
		UUID:       uuid.New(),
		Role:       "testRole",
		Content:    "testContent",
		TokenCount: 1,
		Metadata:   map[string]interface{}{"key": "value"},
	}

	// Call the Create function
	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)
	createdMessage, err := messageDAO.Create(testCtx, message)
	assert.NoError(t, err)

	t.Run("Get should return existing message", func(t *testing.T) {
		// Call the Get function
		retrievedMessage, err := messageDAO.Get(testCtx, createdMessage.UUID)
		assert.NoError(t, err)
		assert.NotNil(t, retrievedMessage)

		// Assert that the returned Message matches the original Message object
		assert.Equal(t, createdMessage.UUID, retrievedMessage.UUID)
		assert.Equal(t, createdMessage.Role, retrievedMessage.Role)
		assert.Equal(t, createdMessage.Content, retrievedMessage.Content)
		assert.Equal(t, createdMessage.TokenCount, retrievedMessage.TokenCount)
		assert.Equal(t, createdMessage.Metadata, retrievedMessage.Metadata)
	})

	t.Run("Get should return ErrNotFound for non-existant message", func(t *testing.T) {
		retrievedMessage, err := messageDAO.Get(testCtx, uuid.New())
		assert.ErrorIs(t, err, models.ErrNotFound)
		assert.Nil(t, retrievedMessage)
	})
}

func TestGetLastN(t *testing.T) {
	sessionID := createSession(t)

	// Initialize a few Message objects with test data
	messages := []models.Message{
		{
			UUID:       uuid.New(),
			Role:       "testRole1",
			Content:    "testContent1",
			TokenCount: 1,
			Metadata:   map[string]interface{}{"key1": "value1"},
		},
		{
			UUID:       uuid.New(),
			Role:       "testRole2",
			Content:    "testContent2",
			TokenCount: 2,
			Metadata:   map[string]interface{}{"key2": "value2"},
		},
		{
			UUID:       uuid.New(),
			Role:       "testRole3",
			Content:    "testContent3",
			TokenCount: 3,
			Metadata:   map[string]interface{}{"key3": "value3"},
		},
	}

	// Call the CreateMany function to store the messages in the database
	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)
	_, err = messageDAO.CreateMany(testCtx, messages)
	assert.NoError(t, err)

	t.Run("GetLastN", func(t *testing.T) {
		// Call the GetLastN function
		lastMessages, err := messageDAO.GetLastN(testCtx, 2, uuid.Nil)

		// Use the assert package to check that the returned slice of Message is not nil and no error is returned
		assert.NoError(t, err)
		assert.NotNil(t, lastMessages)

		// Assert that the returned slice of Message has the correct length and the messages are the last ones created
		assert.Equal(t, 2, len(lastMessages))
		assert.Equal(t, messages[1].UUID, lastMessages[0].UUID)
		assert.Equal(t, messages[2].UUID, lastMessages[1].UUID)
	})

	t.Run("GetLastN with BeforeUUID", func(t *testing.T) {
		// Additional test case for GetLastN with the second message's UUID
		secondMessageUUID := messages[1].UUID
		lastMessages, err := messageDAO.GetLastN(testCtx, 3, secondMessageUUID)
		assert.NoError(t, err)
		assert.NotNil(t, lastMessages)

		// Assert that the returned slice of Message has the correct length and
		// the messages are the second and the last ones created
		assert.Equal(t, 2, len(lastMessages))
		assert.Equal(t, messages[0].UUID, lastMessages[0].UUID)
		assert.Equal(t, messages[1].UUID, lastMessages[1].UUID)
	})

	// Test for non-existant session
	t.Run("GetLastN with non-existent session should return empty slice", func(t *testing.T) {
		// Call the GetLastN function
		messageDAO := &MessageDAO{db: testDB, sessionID: "non-existent-session"}
		lastMessages, err := messageDAO.GetLastN(testCtx, 2, uuid.Nil)

		assert.NoError(t, err)
		assert.Empty(t, lastMessages)
	})
}

func TestGetSinceLastSummary(t *testing.T) {
	sessionID := createSession(t)

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	windowSize := 10 // You can define the windowSize as per your requirement
	var messages = make([]models.Message, windowSize*2)
	for i := 0; i < windowSize*2; i++ {
		messages[i] = models.Message{
			UUID:       uuid.New(),
			Role:       "user",
			Content:    fmt.Sprintf("testContent%d", i),
			TokenCount: 1,
			Metadata:   map[string]interface{}{"key": "value"},
		}
	}
	_, err = messageDAO.CreateMany(testCtx, messages)
	assert.NoError(t, err)

	t.Run("GetSinceLastSummary without Summary", func(t *testing.T) {
		returnedMessages, err := messageDAO.GetSinceLastSummary(testCtx, nil, windowSize)
		assert.NoError(t, err)
		assert.Equal(t, windowSize, len(returnedMessages))
		// the last message returned should be the most recent
		assert.Equal(t, messages[len(messages)-1].UUID, returnedMessages[windowSize-1].UUID)
	})

	t.Run("GetSinceLastSummary with Summary", func(t *testing.T) {
		// insert a summary using the UUID of the windowSize-th message
		summaryPointID := 15
		summaryUUID := messages[summaryPointID-1].UUID
		summary := SummaryStoreSchema{
			SessionID:        sessionID,
			SummaryPointUUID: summaryUUID,
			Content:          "testContent",
		}
		_, err = testDB.NewInsert().Model(&summary).Exec(testCtx)
		assert.NoError(t, err)

		returnedMessages, err := messageDAO.GetSinceLastSummary(testCtx, &models.Summary{
			UUID:             summaryUUID,
			SummaryPointUUID: summaryUUID,
		}, windowSize)
		assert.NoError(t, err)
		assert.Equal(t, len(messages)-summaryPointID, len(returnedMessages))
		assert.Equal(t, messages[summaryPointID].UUID, returnedMessages[0].UUID)
	})

	t.Run("GetSinceLastSummary with non-existent session should return empty slice", func(t *testing.T) {
		// Call the GetSinceLastSummary function
		messageDAO := &MessageDAO{db: testDB, sessionID: "non-existent-session"}
		lastMessages, err := messageDAO.GetSinceLastSummary(testCtx, nil, 2)

		assert.NoError(t, err)
		assert.Empty(t, lastMessages)
	})
}

func TestGetListByUUID(t *testing.T) {
	sessionID := createSession(t)

	// Initialize a MessageDAO
	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	t.Run("No valid message UUIDs should return empty list", func(t *testing.T) {
		retrievedMessages, err := messageDAO.GetListByUUID(testCtx, []uuid.UUID{uuid.New()})
		assert.NoError(t, err)
		assert.Empty(t, retrievedMessages)
	})

	t.Run("GetListByUUID with valid message UUIDs", func(t *testing.T) {
		// Create a list of UUIDs and corresponding messages
		var uuids []uuid.UUID
		var messages []models.Message
		for i := 0; i < 5; i++ {
			uuid := uuid.New()
			uuids = append(uuids, uuid)
			message := models.Message{
				UUID:       uuid,
				Role:       "user",
				Content:    fmt.Sprintf("testContent%d", i),
				TokenCount: 1,
				Metadata:   map[string]interface{}{"key": "value"},
			}
			messages = append(messages, message)
		}

		// Store messages using CreateMany
		_, err = messageDAO.CreateMany(testCtx, messages)
		assert.NoError(t, err)

		// Test GetListByUUID method with only first 3 UUIDs
		uuidsToRetrieve := uuids[:3]
		retrievedMessages, err := messageDAO.GetListByUUID(testCtx, uuidsToRetrieve)
		assert.NoError(t, err)
		// Assert that length of retrieved messages is same as the length of uuidsToRetrieve
		assert.Equal(t, len(uuidsToRetrieve), len(retrievedMessages))

		// Assert retrieved messages match original messages (only for those we retrieved)
		for i, retrievedMessage := range retrievedMessages {
			assert.Equal(t, uuidsToRetrieve[i], retrievedMessage.UUID)
			assert.Equal(t, messages[i].Content, retrievedMessage.Content)
			assert.Equal(t, messages[i].TokenCount, retrievedMessage.TokenCount)
			assert.Equal(t, messages[i].Metadata, retrievedMessage.Metadata)
		}
	})
}

func TestGetListBySession(t *testing.T) {
	sessionID := createSession(t)

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	var messages []models.Message
	totalMessages := 30
	pageSize := 10
	for i := 0; i < totalMessages; i++ {
		uuid := uuid.New()
		message := models.Message{
			UUID:       uuid,
			Role:       "user",
			Content:    fmt.Sprintf("testContent%d", i),
			TokenCount: 1,
			Metadata:   map[string]interface{}{"key": "value"},
		}
		messages = append(messages, message)
	}

	_, err = messageDAO.CreateMany(testCtx, messages)
	assert.NoError(t, err)

	for i := 1; i <= totalMessages/pageSize; i++ {
		t.Run(fmt.Sprintf("page %d", i), func(t *testing.T) {
			retrievedMessages, err := messageDAO.GetListBySession(testCtx, i, pageSize)
			assert.NoError(t, err)
			assert.Equal(t, pageSize, retrievedMessages.RowCount)
			assert.Equal(t, pageSize, len(retrievedMessages.Messages))
			assert.Equal(t, totalMessages, retrievedMessages.TotalCount)
			assert.Equal(t, messages[(i-1)*pageSize].UUID, retrievedMessages.Messages[0].UUID)
			assert.Equal(t, messages[i*pageSize-1].UUID, retrievedMessages.Messages[pageSize-1].UUID)
		})
	}
}

func TestGetListBySession_Nonexistant_Session(t *testing.T) {
	sessionID := testutils.GenerateRandomString(10)
	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	retrievedMessages, err := messageDAO.GetListBySession(testCtx, 0, 10)
	assert.NoError(t, err)
	assert.NotNil(t, retrievedMessages)
	assert.Empty(t, 0, retrievedMessages.Messages)
	assert.Equal(t, 0, retrievedMessages.RowCount)
	assert.Equal(t, 0, retrievedMessages.TotalCount)
}

func runSubTest(t *testing.T, messageDAO *MessageDAO,
	includeContent, privileged bool, expectedMessage *models.Message, updatedMessage *models.Message) {
	t.Helper()
	err := messageDAO.Update(testCtx, updatedMessage, includeContent, privileged)
	assert.NoError(t, err)
	retrievedMessage, err := messageDAO.Get(testCtx, updatedMessage.UUID)
	assert.NoError(t, err)
	assert.Equal(t, expectedMessage.UUID, retrievedMessage.UUID)
	assert.Equal(t, expectedMessage.Content, retrievedMessage.Content)
	assert.Equal(t, expectedMessage.TokenCount, retrievedMessage.TokenCount)
	assert.Equal(t, expectedMessage.Metadata, retrievedMessage.Metadata)
}

func TestUpdate(t *testing.T) {
	sessionID := createSession(t)

	// Initialize a MessageDAO
	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	message := models.Message{
		Role:       "user",
		Content:    "testContent",
		TokenCount: 1,
		Metadata:   map[string]interface{}{"key1": "value1", "keyOther": "valueOther"},
	}

	testCases := []struct {
		name            string
		updatedMessage  models.Message
		expectedMessage models.Message
		includeContent  bool
		privileged      bool
	}{
		{
			name: "UpdateMessages with unprivileged & includeContent",
			updatedMessage: models.Message{
				Role:       "user2",
				Content:    "testContentUpdated",
				TokenCount: 2,
				Metadata:   map[string]interface{}{"key1": "value1Updated", "key2": "value2", "system": "privileged"},
			},
			expectedMessage: models.Message{
				Role:       "user2",
				Content:    "testContentUpdated",
				TokenCount: 2,
				Metadata:   map[string]interface{}{"key1": "value1Updated", "key2": "value2", "keyOther": "valueOther"},
			},
			includeContent: true,
			privileged:     false,
		},
		{
			name: "UpdateMessages with privileged",
			updatedMessage: models.Message{
				Role:       "user2",
				Content:    "testContentUpdated",
				TokenCount: 2,
				Metadata:   map[string]interface{}{"key1": "value1Updated", "key2": "value2", "system": "privileged"},
			},
			expectedMessage: models.Message{
				Role:       "user2",
				Content:    "testContentUpdated",
				TokenCount: 2,
				Metadata:   map[string]interface{}{"key1": "value1Updated", "key2": "value2", "keyOther": "valueOther", "system": "privileged"},
			},
			includeContent: true,
			privileged:     true,
		},
		{
			name: "UpdateMessages with includeContent false",
			updatedMessage: models.Message{
				Role:       "user2",
				Content:    "testContentUpdated",
				TokenCount: 2,
				Metadata:   map[string]interface{}{"key1": "value1Updated", "key2": "value2", "system": "privileged"},
			},
			expectedMessage: models.Message{
				Role:       "user",
				Content:    "testContent",
				TokenCount: 2,
				Metadata:   map[string]interface{}{"key1": "value1Updated", "key2": "value2", "keyOther": "valueOther", "system": "privileged"},
			},
			includeContent: false,
			privileged:     true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			createdMessage, err := messageDAO.Create(testCtx, &message)
			assert.NoError(t, err)

			tc.updatedMessage.UUID = createdMessage.UUID
			tc.expectedMessage.UUID = createdMessage.UUID

			runSubTest(t, messageDAO, tc.includeContent, tc.privileged, &tc.expectedMessage, &tc.updatedMessage)
		})
	}
}

func TestUpdateMany(t *testing.T) {
	sessionID := createSession(t)

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	messages := make([]models.Message, 5)
	for i := 0; i < 5; i++ {
		message := models.Message{
			Role:       "user",
			Content:    fmt.Sprintf("testContent%d", i),
			TokenCount: 1,
			Metadata:   map[string]interface{}{fmt.Sprintf("key%d", i): fmt.Sprintf("value%d", i)},
		}
		messages[i] = message
	}

	updateMessages := func(messages []models.Message) []models.Message {
		updatedMessages := make([]models.Message, 3)
		for i := 0; i < 3; i++ {
			updatedMessage := models.Message{
				UUID:       messages[i].UUID,
				Role:       "user",
				Content:    fmt.Sprintf("updatedContent%d", i),
				TokenCount: messages[i].TokenCount + 1,
				Metadata:   map[string]interface{}{fmt.Sprintf("key%d", i): fmt.Sprintf("updatedValue%d", i), "newKey": "newValue"},
			}
			updatedMessages[i] = updatedMessage
		}
		return updatedMessages
	}

	t.Run("UpdateMany with unprivileged & includeContent", func(t *testing.T) {
		createdMessages, err := messageDAO.CreateMany(testCtx, messages)
		assert.NoError(t, err)

		updatedMessages := updateMessages(createdMessages)
		err = messageDAO.UpdateMany(testCtx, updatedMessages, true, false)
		assert.NoError(t, err)

		for _, updatedMessage := range updatedMessages {
			retrievedMessage, err := messageDAO.Get(testCtx, updatedMessage.UUID)
			assert.NoError(t, err)

			assert.Equal(t, updatedMessage.UUID, retrievedMessage.UUID)
			assert.Equal(t, updatedMessage.Content, retrievedMessage.Content)
			assert.Equal(t, updatedMessage.TokenCount, retrievedMessage.TokenCount)
			assert.Equal(t, updatedMessage.Metadata, retrievedMessage.Metadata)
			for key, value := range updatedMessage.Metadata {
				assert.Equal(t, value, retrievedMessage.Metadata[key])
			}
		}
	})

	t.Run("UpdateMany with includedContent false", func(t *testing.T) {
		createdMessages, err := messageDAO.CreateMany(testCtx, messages)
		assert.NoError(t, err)

		updatedMessages := updateMessages(createdMessages)
		err = messageDAO.UpdateMany(testCtx, updatedMessages, false, false)
		assert.NoError(t, err)

		for i, updatedMessage := range updatedMessages {
			retrievedMessage, err := messageDAO.Get(testCtx, updatedMessage.UUID)
			assert.NoError(t, err)

			assert.Equal(t, updatedMessage.UUID, retrievedMessage.UUID)
			assert.Equal(t, messages[i].Role, retrievedMessage.Role)       // same as original
			assert.Equal(t, messages[i].Content, retrievedMessage.Content) // same as original
			assert.Equal(t, updatedMessage.TokenCount, retrievedMessage.TokenCount)
			assert.Equal(t, updatedMessage.Metadata, retrievedMessage.Metadata)
			for key, value := range updatedMessage.Metadata {
				assert.Equal(t, value, retrievedMessage.Metadata[key])
			}
		}
	})
}

func TestDelete(t *testing.T) {
	sessionID := createSession(t)

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	messageUUID := uuid.New()
	message := models.Message{
		UUID:       messageUUID,
		Role:       "user",
		Content:    "testContent",
		TokenCount: 1,
		Metadata:   map[string]interface{}{"key": "value"},
	}

	m, err := messageDAO.Create(testCtx, &message)
	assert.NoError(t, err)

	embeddings := []models.TextData{
		{
			TextUUID:  m.UUID,
			Text:      "testText",
			Embedding: genTestVector(t, 1536),
		},
	}
	err = messageDAO.CreateEmbeddings(testCtx, embeddings)
	assert.NoError(t, err)

	err = messageDAO.Delete(testCtx, messageUUID)
	assert.NoError(t, err)

	_, err = messageDAO.GetEmbedding(testCtx, messageUUID)
	assert.ErrorIs(t, err, models.ErrNotFound)

	_, err = messageDAO.Get(testCtx, messageUUID)
	assert.ErrorIs(t, err, models.ErrNotFound)
}

func genTestVector(t *testing.T, width int) []float32 {
	t.Helper()
	vector := make([]float32, width)
	for i := range vector {
		vector[i] = rand.Float32()
	}
	return vector
}

func TestCreateEmbeddings(t *testing.T) {
	sessionID := createSession(t)

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	messages := make([]models.Message, 5)
	for i := 0; i < 5; i++ {
		message := models.Message{
			UUID:       uuid.New(),
			Role:       "user",
			Content:    fmt.Sprintf("testContent%d", i),
			TokenCount: 1,
			Metadata:   map[string]interface{}{fmt.Sprintf("key%d", i): fmt.Sprintf("value%d", i)},
		}
		messages[i] = message
	}

	_, err = messageDAO.CreateMany(testCtx, messages)
	assert.NoError(t, err)

	embeddings := []models.TextData{
		{
			TextUUID:  messages[0].UUID,
			Text:      "testText1",
			Embedding: genTestVector(t, 1536),
		},
		{
			TextUUID:  messages[1].UUID,
			Text:      "testText2",
			Embedding: genTestVector(t, 1536),
		},
	}

	err = messageDAO.CreateEmbeddings(testCtx, embeddings)
	assert.NoError(t, err)

	for _, message := range embeddings {
		textData, err := messageDAO.GetEmbedding(testCtx, message.TextUUID)
		assert.NoError(t, err)
		assert.NotNil(t, textData)
		assert.Equal(t, message.Embedding, textData.Embedding)
	}
}

func TestGetEmbedding(t *testing.T) {
	sessionID := createSession(t)

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	messageUUID := uuid.New()
	message := models.Message{
		UUID:       messageUUID,
		Role:       "user",
		Content:    "testContent",
		TokenCount: 1,
		Metadata:   map[string]interface{}{"key": "value"},
	}

	m, err := messageDAO.Create(testCtx, &message)
	assert.NoError(t, err)

	embeddings := []models.TextData{
		{
			TextUUID:  m.UUID,
			Text:      "testText",
			Embedding: genTestVector(t, 1536),
		},
	}
	err = messageDAO.CreateEmbeddings(testCtx, embeddings)
	assert.NoError(t, err)

	textData, err := messageDAO.GetEmbedding(testCtx, messageUUID)
	assert.NoError(t, err)
	assert.NotNil(t, textData)
	assert.Equal(t, embeddings[0].Embedding, textData.Embedding)
}

func TestGetEmbeddingListBySession(t *testing.T) {
	sessionID := createSession(t)

	messageDAO, err := NewMessageDAO(testDB, appState, sessionID)
	assert.NoError(t, err)

	messages := make([]models.Message, 5)
	for i := 0; i < 5; i++ {
		message := models.Message{
			UUID:       uuid.New(),
			Role:       "user",
			Content:    fmt.Sprintf("testContent%d", i),
			TokenCount: 1,
			Metadata:   map[string]interface{}{fmt.Sprintf("key%d", i): fmt.Sprintf("value%d", i)},
		}
		messages[i] = message
	}

	_, err = messageDAO.CreateMany(testCtx, messages)
	assert.NoError(t, err)

	embeddings := []models.TextData{
		{
			TextUUID:  messages[0].UUID,
			Text:      "testText1",
			Embedding: genTestVector(t, 1536),
		},
		{
			TextUUID:  messages[1].UUID,
			Text:      "testText2",
			Embedding: genTestVector(t, 1536),
		},
	}

	err = messageDAO.CreateEmbeddings(testCtx, embeddings)
	assert.NoError(t, err)

	textDataList, err := messageDAO.GetEmbeddingListBySession(testCtx)
	assert.NoError(t, err)
	assert.NotNil(t, textDataList)
	assert.Equal(t, len(embeddings), len(textDataList))
	assert.Equal(t, embeddings[0].Embedding, textDataList[0].Embedding)
	assert.Equal(t, embeddings[1].Embedding, textDataList[1].Embedding)
}
