package server

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/store/postgres"
	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/assert"
)

func TestCreateUserHandler(t *testing.T) {
	// Initialize the UserStoreDAO
	userStore := postgres.NewUserStoreDAO(testDB)
	appState := &models.AppState{UserStore: userStore}

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
	req, err := http.NewRequest("POST", "/api/v1/user", bytes.NewBuffer(userJSON))
	assert.NoError(t, err)

	// Create a ResponseRecorder to record the response
	rr := httptest.NewRecorder()

	// Create the handler
	handler := CreateUserHandler(appState)

	// Serve the request
	handler.ServeHTTP(rr, req)

	// Check the status code
	assert.Equal(t, http.StatusCreated, rr.Code)

	// Check the response body
	expectedUser := new(models.User)
	decodeRecordedResponse(t, rr, expectedUser)

	assert.NotEmpty(t, expectedUser.UUID)
	assert.Equal(t, expectedUser.UserID, userID)
	assert.Equal(t, expectedUser.Metadata["key"], "value")
}

func TestGetUserHandler(t *testing.T) {
	// Initialize the UserStoreDAO
	userStore := postgres.NewUserStoreDAO(testDB)
	appState := &models.AppState{UserStore: userStore}

	userID := testutils.GenerateRandomString(10)

	// Create a user
	user := &models.CreateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Create the user in the store
	_, err := userStore.Create(testCtx, user)
	assert.NoError(t, err)

	// Create a request
	req, err := http.NewRequest("GET", "/api/v1/user/"+userID, nil)
	assert.NoError(t, err)

	// Create a ResponseRecorder to record the response
	rr := httptest.NewRecorder()

	// Create the handler
	handler := GetUserHandler(appState)

	// Create a router to get the URL parameters
	r := chi.NewRouter()
	r.Get("/api/v1/user/{userId}", handler)

	// Serve the request
	r.ServeHTTP(rr, req)

	// Check the status code
	assert.Equal(t, http.StatusOK, rr.Code)

	// Check the response body
	expectedUser := new(models.User)
	decodeRecordedResponse(t, rr, expectedUser)

	assert.NotEmpty(t, expectedUser.UUID)
	assert.Equal(t, expectedUser.UserID, userID)
	assert.Equal(t, expectedUser.Metadata["key"], "value")
}

func TestUpdateUserHandler(t *testing.T) {
	// Initialize the UserStoreDAO
	userStore := postgres.NewUserStoreDAO(testDB)
	appState := &models.AppState{UserStore: userStore}

	// Create a user
	userID := testutils.GenerateRandomString(10)
	user := &models.CreateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Create the user in the store
	_, err := userStore.Create(testCtx, user)
	assert.NoError(t, err)

	// Update the user
	updateUser := &models.UpdateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "new value",
		},
	}

	// Convert updateUser to JSON
	updateUserJSON, err := json.Marshal(updateUser)
	assert.NoError(t, err)

	// Create a request
	req, err := http.NewRequest("PATCH", "/api/v1/user/"+userID, bytes.NewBuffer(updateUserJSON))
	assert.NoError(t, err)

	// Create a ResponseRecorder to record the response
	rr := httptest.NewRecorder()

	// Create the handler
	handler := UpdateUserHandler(appState)

	// Create a router to get the URL parameters
	r := chi.NewRouter()
	r.Patch("/api/v1/user/{userId}", handler)

	// Serve the request
	r.ServeHTTP(rr, req)

	// Check the status code
	assert.Equal(t, http.StatusOK, rr.Code)

	// Fetch the user and check the updated fields
	fetchedUser, err := userStore.Get(testCtx, userID)
	assert.NoError(t, err)
	assert.Equal(t, fetchedUser.Metadata["key"], "new value")
}

func TestDeleteUserHandler(t *testing.T) {
	// Initialize the UserStoreDAO
	userStore := postgres.NewUserStoreDAO(testDB)
	appState := &models.AppState{UserStore: userStore}

	// Create a user
	userID := testutils.GenerateRandomString(10)
	user := &models.CreateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Create the user in the store
	_, err := userStore.Create(testCtx, user)
	assert.NoError(t, err)

	// Create a request to delete the user
	req, err := http.NewRequest("DELETE", "/api/v1/user/"+userID, nil)
	assert.NoError(t, err)

	// Create a ResponseRecorder to record the response
	rr := httptest.NewRecorder()

	// Create the handler
	handler := DeleteUserHandler(appState)

	// Create a router to get the URL parameters
	r := chi.NewRouter()
	r.Delete("/api/v1/user/{userId}", handler)

	// Serve the request
	r.ServeHTTP(rr, req)

	// Check the status code
	assert.Equal(t, http.StatusOK, rr.Code)

	// Try to fetch the user and check that it does not exist
	_, err = userStore.Get(testCtx, userID)
	assert.ErrorIs(t, err, models.ErrNotFound)
}
