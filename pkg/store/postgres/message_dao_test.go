package postgres

import (
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
	// TODO: Initialize a MessageDAO and a memory window

	// TODO: Call GetSinceLastSummary with the memory window

	// TODO: Assert that the returned slice of messages is not nil and no error is returned
}

func TestGetListByUUID(t *testing.T) {
	// TODO: Initialize a MessageDAO and a slice of message UUIDs

	// TODO: Call GetListByUUID with the slice of UUIDs

	// TODO: Assert that the returned slice of messages is not nil and no error is returned
}

func TestGetListBySession(t *testing.T) {
	// TODO: Initialize a MessageDAO, a current page, and a page size

	// TODO: Call GetListBySession with the current page and page size

	// TODO: Assert that the returned MessageListResponse is not nil and no error is returned
}

func TestUpdate(t *testing.T) {
	// TODO: Initialize a MessageDAO, a message, and a boolean for isPrivileged

	// TODO: Call Update with the message and isPrivileged

	// TODO: Assert that no error is returned
}

func TestUpdateMany(t *testing.T) {
	// TODO: Initialize a MessageDAO, a slice of messages, and a boolean for isPrivileged

	// TODO: Call UpdateMany with the slice of messages and isPrivileged

	// TODO: Assert that no error is returned
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
