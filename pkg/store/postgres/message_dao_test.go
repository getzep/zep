package postgres

import (
	"fmt"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"testing"
)

func TestNewMessageDAO(t *testing.T) {
	sessionID := "testSessionID"

	// Call the NewMessageDAO function
	messageDAO, err := NewMessageDAO(testDB, sessionID)
	assert.NoError(t, err)
	assert.NotNil(t, messageDAO)

	// New test case for empty sessionID
	emptySessionID := ""
	messageDAO, err = NewMessageDAO(testDB, emptySessionID)
	assert.Error(t, err)
	assert.Nil(t, messageDAO)
}

func TestCreate(t *testing.T) {
	// Initialize the database connection and session ID
	sessionID := testutils.GenerateRandomString(10)

	// Try Update the session first. If no rows are affected, create a new session.
	sessionStore := NewSessionDAO(testDB)
	_, err := sessionStore.Create(testCtx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	assert.NoError(t, err)

	// Initialize a Message object with test data
	message := &models.Message{
		UUID:       uuid.New(),
		Role:       "testRole",
		Content:    "testContent",
		TokenCount: 1,
		Metadata:   map[string]interface{}{"key": "value"},
	}

	// Call the Create function
	messageDAO, err := NewMessageDAO(testDB, sessionID)
	assert.NoError(t, err)
	createdMessage, err := messageDAO.Create(testCtx, message)
	assert.NoError(t, err)

	// Assert that the created message matches the original Message object
	assert.NoError(t, err)
	assert.Equal(t, message.UUID, createdMessage.UUID)
	assert.Equal(t, message.Role, createdMessage.Role)
	assert.Equal(t, message.Content, createdMessage.Content)
	assert.Equal(t, message.TokenCount, createdMessage.TokenCount)
	assert.Equal(t, message.Metadata, createdMessage.Metadata)
}

func TestCreateMany(t *testing.T) {
	// Initialize the database connection and session ID
	sessionID := testutils.GenerateRandomString(10)

	// Try Update the session first. If no rows are affected, create a new session.
	sessionStore := NewSessionDAO(testDB)
	_, err := sessionStore.Create(testCtx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	assert.NoError(t, err)

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
	messageDAO, err := NewMessageDAO(testDB, sessionID)
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
	// Initialize the database connection and session ID
	sessionID := testutils.GenerateRandomString(10)

	// Try Update the session first. If no rows are affected, create a new session.
	sessionStore := NewSessionDAO(testDB)
	_, err := sessionStore.Create(testCtx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	assert.NoError(t, err)

	// Initialize a Message object with test data
	message := &models.Message{
		UUID:       uuid.New(),
		Role:       "testRole",
		Content:    "testContent",
		TokenCount: 1,
		Metadata:   map[string]interface{}{"key": "value"},
	}

	// Call the Create function
	messageDAO, err := NewMessageDAO(testDB, sessionID)
	assert.NoError(t, err)
	createdMessage, err := messageDAO.Create(testCtx, message)
	assert.NoError(t, err)

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
}

func TestGetLastN(t *testing.T) {
	// Initialize the database connection and session ID
	sessionID := testutils.GenerateRandomString(10)

	// Try Update the session first. If no rows are affected, create a new session.
	sessionStore := NewSessionDAO(testDB)
	_, err := sessionStore.Create(testCtx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	assert.NoError(t, err)

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
	messageDAO, err := NewMessageDAO(testDB, sessionID)
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

}

func TestGetSinceLastSummary(t *testing.T) {
	// Initialize the database connection and session ID
	sessionID := testutils.GenerateRandomString(10)

	// Try Update the session first. If no rows are affected, create a new session.
	sessionStore := NewSessionDAO(testDB)
	_, err := sessionStore.Create(testCtx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	assert.NoError(t, err)

	// Initialize a MessageDAO
	messageDAO, err := NewMessageDAO(testDB, sessionID)
	assert.NoError(t, err)

	// Insert messages in the DAO to test GetSinceLastSummary
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

	// insert a summary using the UUID of the windowSize-th message
	summaryUUID := messages[windowSize-1].UUID
	summary := SummaryStoreSchema{
		SessionID:        sessionID,
		SummaryPointUUID: summaryUUID,
		Content:          "testContent",
	}
	_, err = testDB.NewInsert().Model(&summary).Exec(testCtx)
	assert.NoError(t, err)

	// Call GetSinceLastSummary
	returnedMessages, err := messageDAO.GetSinceLastSummary(testCtx, windowSize)
	assert.NoError(t, err)
	assert.Equal(t, windowSize, len(returnedMessages))
	assert.Equal(t, messages[windowSize].UUID, returnedMessages[0].UUID)
}

func TestGetListByUUID(t *testing.T) {
	// Initialize the database connection and session ID
	sessionID := testutils.GenerateRandomString(10)

	// Try Update the session first. If no rows are affected, create a new session.
	sessionStore := NewSessionDAO(testDB)
	_, err := sessionStore.Create(testCtx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	assert.NoError(t, err)

	// Initialize a MessageDAO
	messageDAO, err := NewMessageDAO(testDB, sessionID)
	assert.NoError(t, err)

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
}

func TestGetListBySession(t *testing.T) {
	sessionID := testutils.GenerateRandomString(10)

	sessionStore := NewSessionDAO(testDB)
	_, err := sessionStore.Create(testCtx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	assert.NoError(t, err)

	messageDAO, err := NewMessageDAO(testDB, sessionID)
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

func runSubTest(t *testing.T, messageDAO *MessageDAO, privileged bool, expectedMetadata map[string]interface{}, updatedMessage *models.Message) {
	t.Helper()
	err := messageDAO.Update(testCtx, updatedMessage, privileged)
	assert.NoError(t, err)
	retrievedMessage, err := messageDAO.Get(testCtx, updatedMessage.UUID)
	assert.NoError(t, err)
	assert.Equal(t, updatedMessage.UUID, retrievedMessage.UUID)
	assert.Equal(t, updatedMessage.Content, retrievedMessage.Content)
	assert.Equal(t, updatedMessage.TokenCount, retrievedMessage.TokenCount)
	assert.Equal(t, expectedMetadata, retrievedMessage.Metadata)
}

func TestUpdate(t *testing.T) {
	// Initialize the database connection and session ID
	sessionID := testutils.GenerateRandomString(10)

	// Try Update the session first. If no rows are affected, create a new session.
	sessionStore := NewSessionDAO(testDB)
	_, err := sessionStore.Create(testCtx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	assert.NoError(t, err)

	// Initialize a MessageDAO
	messageDAO, err := NewMessageDAO(testDB, sessionID)
	assert.NoError(t, err)

	// Create a message and store it
	uuid := uuid.New()
	message := models.Message{
		UUID:       uuid,
		Role:       "user",
		Content:    "testContent",
		TokenCount: 1,
		Metadata:   map[string]interface{}{"key1": "value1", "keyOther": "valueOther"},
	}

	// Store messages
	_, err = messageDAO.Create(testCtx, &message)
	assert.NoError(t, err)

	t.Run("Update with unprivileged", func(t *testing.T) {
		updatedMessage := models.Message{
			UUID:       uuid,
			Role:       "user",
			Content:    "testContentUpdated",
			TokenCount: 2,
			Metadata:   map[string]interface{}{"key1": "value1Updated", "key2": "value2", "system": "privileged"},
		}
		// expectedMetadata should not contain the `system` key
		expectedMetadata := map[string]interface{}{
			"key1":     "value1Updated",
			"key2":     "value2",
			"keyOther": "valueOther",
		}
		runSubTest(t, messageDAO, false, expectedMetadata, &updatedMessage)
	})

	t.Run("Update with privileged", func(t *testing.T) {
		updatedMessage := models.Message{
			UUID:       uuid,
			Role:       "user",
			Content:    "testContentUpdated",
			TokenCount: 2,
			Metadata:   map[string]interface{}{"key1": "value1Updated", "key2": "value2", "system": "privileged"},
		}
		expectedMetadata := map[string]interface{}{
			"key1":     "value1Updated",
			"key2":     "value2",
			"keyOther": "valueOther",
			"system":   "privileged",
		}
		runSubTest(t, messageDAO, true, expectedMetadata, &updatedMessage)
	})
}

func TestUpdateMany(t *testing.T) {
	sessionID := testutils.GenerateRandomString(10)

	sessionStore := NewSessionDAO(testDB)
	_, err := sessionStore.Create(testCtx, &models.CreateSessionRequest{
		SessionID: sessionID,
	})
	assert.NoError(t, err)

	messageDAO, err := NewMessageDAO(testDB, sessionID)
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

	// Create updated versions of the first 3 messages
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

	err = messageDAO.UpdateMany(testCtx, updatedMessages, false)
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
}

func TestDelete(t *testing.T) {
	// TODO: Initialize a MessageDAO and a message UUID

	// TODO: Call Delete with the message UUID

	// TODO: Assert that no error is returned
}

func TestCreateEmbeddings(t *testing.T) {
	// TODO: Initialize a MessageDAO and a slice of TextData

	// TODO: Call CreateEmbeddings with the slice of TextData

	// TODO: Assert that no error is returned
}

func TestGetEmbedding(t *testing.T) {
	// TODO: Initialize a MessageDAO and a message UUID

	// TODO: Call GetEmbedding with the message UUID

	// TODO: Assert that the returned TextData is not nil and no error is returned
}

func TestGetEmbeddingListBySession(t *testing.T) {
	// TODO: Initialize a MessageDAO

	// TODO: Call GetEmbeddingListBySession

	// TODO: Assert that the returned slice of TextData is not nil and no error is returned
}
