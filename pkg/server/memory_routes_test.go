package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"testing"

	"github.com/getzep/zep/pkg/store/postgres"
	"github.com/getzep/zep/pkg/testutils"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
)

func TestGetSessionRoute(t *testing.T) {
	// Initialize the SessionStoreDAO
	sessionStore := postgres.NewSessionDAO(testDB)

	// Create a session
	sessionID := testutils.GenerateRandomString(10)
	session := &models.CreateSessionRequest{
		SessionID: sessionID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Create the session in the store
	_, err := sessionStore.Create(testCtx, session)
	assert.NoError(t, err)

	// Create a request
	req, err := http.NewRequest("GET", testServer.URL+"/api/v1/sessions/"+sessionID, nil)
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusOK, resp.StatusCode)

	// Check the response body
	expectedSession := new(models.Session)
	err = json.NewDecoder(resp.Body).Decode(expectedSession)
	assert.NoError(t, err)

	assert.NotEmpty(t, expectedSession.UUID)
	assert.Equal(t, expectedSession.SessionID, sessionID)
	assert.Equal(t, expectedSession.Metadata["key"], "value")
}

func TestCreateSessionRoute(t *testing.T) {
	sessionStore := postgres.NewSessionDAO(testDB)

	// Create a session
	sessionID := testutils.GenerateRandomString(10)
	session := &models.CreateSessionRequest{
		SessionID: sessionID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Convert session to JSON
	sessionJSON, err := json.Marshal(session)
	assert.NoError(t, err)

	// Create a request
	req, err := http.NewRequest(
		"POST",
		testServer.URL+"/api/v1/sessions",
		bytes.NewBuffer(sessionJSON),
	)
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusCreated, resp.StatusCode)

	// Retrieve the session from the store
	createdSession, err := sessionStore.Get(testCtx, sessionID)
	assert.NoError(t, err)

	// Check the created session
	assert.NotEmpty(t, createdSession.UUID)
	assert.Equal(t, createdSession.SessionID, sessionID)
	assert.Equal(t, createdSession.Metadata["key"], "value")
}

func TestUpdateSessionRoute(t *testing.T) {
	// Initialize the SessionStoreDAO
	sessionStore := postgres.NewSessionDAO(testDB)

	// Create a session
	sessionID := testutils.GenerateRandomString(10)
	session := &models.CreateSessionRequest{
		SessionID: sessionID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Create the session in the store
	_, err := sessionStore.Create(testCtx, session)
	assert.NoError(t, err)

	// Update the session
	updateSession := &models.UpdateSessionRequest{
		SessionID: sessionID,
		Metadata: map[string]interface{}{
			"key": "new value",
		},
	}

	// Convert updateSession to JSON
	updateSessionJSON, err := json.Marshal(updateSession)
	assert.NoError(t, err)

	// Create a request
	req, err := http.NewRequest(
		"PATCH",
		testServer.URL+"/api/v1/sessions/"+sessionID, // Use the server Path here
		bytes.NewBuffer(updateSessionJSON),
	)
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusOK, resp.StatusCode)

	// Retrieve the session from the store
	updatedSession, err := sessionStore.Get(testCtx, sessionID)
	assert.NoError(t, err)

	// Check the updated session
	assert.NotEmpty(t, updatedSession.UUID)
	assert.Equal(t, updatedSession.SessionID, sessionID)
	assert.Equal(t, updatedSession.Metadata["key"], "new value")
}

func TestGetSessionListRoute(t *testing.T) {
	postgres.CleanDB(t, testDB)
	err := postgres.CreateSchema(testCtx, appState, testDB)
	assert.NoError(t, err)
	// Initialize the SessionStoreDAO
	sessionStore := postgres.NewSessionDAO(testDB)

	// Create multiple sessions
	numSessions := 5
	for i := 0; i < numSessions; i++ {
		sessionID := testutils.GenerateRandomString(10)
		session := &models.CreateSessionRequest{
			SessionID: sessionID,
			Metadata: map[string]interface{}{
				"key": "value",
			},
		}

		// Create the session in the store
		_, err := sessionStore.Create(testCtx, session)
		assert.NoError(t, err)
	}

	// Create a request
	req, err := http.NewRequest("GET", testServer.URL+"/api/v1/sessions", nil)
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusOK, resp.StatusCode)

	// Check the response body
	var sessions []models.Session
	err = json.NewDecoder(resp.Body).Decode(&sessions)
	assert.NoError(t, err)

	// Check the number of sessions returned
	assert.Equal(t, numSessions, len(sessions))
}
