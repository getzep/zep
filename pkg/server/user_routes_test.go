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

func TestCreateUserRoute(t *testing.T) {
	userID := testutils.GenerateRandomString(10)

	// Create a user
	user := &models.CreateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Convert user to JSON
	userJSON, err := json.Marshal(user)
	assert.NoError(t, err)

	// Create a request
	req, err := http.NewRequest("POST", testServer.URL+"/api/v1/user", bytes.NewBuffer(userJSON))
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusCreated, resp.StatusCode)

	// Check the response body
	expectedUser := new(models.User)
	err = json.NewDecoder(resp.Body).Decode(expectedUser)
	assert.NoError(t, err)

	assert.NotEmpty(t, expectedUser.UUID)
	assert.Equal(t, expectedUser.UserID, userID)
	assert.Equal(t, expectedUser.Metadata["key"], "value")
}

func TestGetUserRoute(t *testing.T) {
	userID := testutils.GenerateRandomString(10)

	// Create a user
	user := &models.CreateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Create the user in the store
	_, err := testUserStore.Create(testCtx, user)
	assert.NoError(t, err)

	// Create a request
	req, err := http.NewRequest("GET", testServer.URL+"/api/v1/user/"+userID, nil)
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusOK, resp.StatusCode)

	// Check the response body
	resultingUser := new(models.User)
	err = json.NewDecoder(resp.Body).Decode(resultingUser)
	assert.NoError(t, err)

	assert.NotEmpty(t, resultingUser.UUID)
	assert.Equal(t, resultingUser.UserID, userID)
	assert.Equal(t, resultingUser.Metadata["key"], "value")
}

func TestUpdateUserRoute(t *testing.T) {
	// Create a user
	userID := testutils.GenerateRandomString(10)
	user := &models.CreateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Create the user in the store
	_, err := testUserStore.Create(testCtx, user)
	assert.NoError(t, err)

	// Update the user
	updateUser := &models.UpdateUserRequest{
		UserID: userID,
		Email:  "test@example.com",
		Metadata: map[string]interface{}{
			"key": "new value",
		},
	}

	// Convert updateUser to JSON
	updateUserJSON, err := json.Marshal(updateUser)
	assert.NoError(t, err)

	// Create a request
	req, err := http.NewRequest(
		"PATCH",
		testServer.URL+"/api/v1/user/"+userID,
		bytes.NewBuffer(updateUserJSON),
	)
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusOK, resp.StatusCode)

	// Check the response body
	updatedUser := new(models.User)
	err = json.NewDecoder(resp.Body).Decode(updatedUser)
	assert.NoError(t, err)

	// Check the updated fields
	assert.Equal(t, updatedUser.UserID, userID)
	assert.Equal(t, updatedUser.Email, updateUser.Email)
	assert.Equal(t, updatedUser.Metadata["key"], "new value")
}

func TestDeleteUserRoute(t *testing.T) {
	// Create a user
	userID := testutils.GenerateRandomString(10)
	user := &models.CreateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Create the user in the store
	_, err := testUserStore.Create(testCtx, user)
	assert.NoError(t, err)

	// Create a request to delete the user
	req, err := http.NewRequest("DELETE", testServer.URL+"/api/v1/user/"+userID, nil)
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusOK, resp.StatusCode)

	// Try to fetch the user and check that it does not exist
	_, err = testUserStore.Get(testCtx, userID)
	assert.ErrorIs(t, err, models.ErrNotFound)
}

func TestListAllUsersRoute(t *testing.T) {
	postgres.CleanDB(t, testDB)
	err := postgres.CreateSchema(testCtx, appState, testDB)
	assert.NoError(t, err)
	// Create a few users
	for i := 0; i < 5; i++ {
		userID := testutils.GenerateRandomString(10)
		user := &models.CreateUserRequest{
			UserID: userID,
			Metadata: map[string]interface{}{
				"key": "value",
			},
		}

		// Create the user in the store
		_, err := testUserStore.Create(testCtx, user)
		assert.NoError(t, err)
	}

	// Create a request to list the users
	req, err := http.NewRequest("GET", testServer.URL+"/api/v1/user?cursor=0&limit=10", nil)
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusOK, resp.StatusCode)

	// Check the response body
	var users []*models.User
	err = json.NewDecoder(resp.Body).Decode(&users)
	assert.NoError(t, err)

	// Check the number of users returned
	assert.Equal(t, 5, len(users))
}

func TestListUserSessionsRoute(t *testing.T) {
	// Initialize the UserStoreDAO and SessionStoreDAO
	userStore := postgres.NewUserStoreDAO(testDB)
	sessionStore := postgres.NewSessionDAO(testDB)

	// Create a user
	userID := testutils.GenerateRandomString(10)
	user := &models.CreateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Create the user in the store
	createdUser, err := userStore.Create(testCtx, user)
	assert.NoError(t, err)

	// Create a few sessions for the user
	for i := 0; i < 3; i++ {
		sessionID := testutils.GenerateRandomString(10)
		session := &models.CreateSessionRequest{
			SessionID: sessionID,
			UserID:    &createdUser.UserID,
			Metadata: map[string]interface{}{
				"key": "value",
			},
		}

		// Create the session in the store
		_, err := sessionStore.Create(testCtx, session)
		assert.NoError(t, err)
	}

	// Create a request to list the sessions
	req, err := http.NewRequest("GET", testServer.URL+"/api/v1/user/"+userID+"/sessions", nil)
	assert.NoError(t, err)

	// Create a client and do the request
	client := &http.Client{}
	resp, err := client.Do(req)
	assert.NoError(t, err)

	// Check the status code
	assert.Equal(t, http.StatusOK, resp.StatusCode)

	// Check the response body
	var sessions []*models.Session
	err = json.NewDecoder(resp.Body).Decode(&sessions)
	assert.NoError(t, err)

	// Check the number of sessions returned
	assert.Equal(t, 3, len(sessions))

	// Check that the sessions belong to the user
	for _, session := range sessions {
		assert.Equal(t, createdUser.UserID, *session.UserID)
	}
}
