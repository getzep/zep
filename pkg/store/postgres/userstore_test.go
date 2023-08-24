package postgres

import (
	"context"
	"testing"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
)

func TestUserStoreDAO(t *testing.T) {
	ctx := context.Background()

	userID, err := testutils.GenerateRandomSessionID(16)
	assert.NoError(t, err, "GenerateRandomSessionID should not return an error")

	// Initialize the UserStoreDAO
	userStore := NewUserStoreDAO(testDB)

	// Create a user
	user := &models.CreateUserRequest{
		UserID: userID,
		Metadata: map[string]interface{}{
			"key": "value",
		},
	}

	// Test Create
	t.Run("Create", func(t *testing.T) {
		_, err := userStore.Create(ctx, user)
		assert.NoError(t, err)
	})

	// Test Get
	t.Run("Get", func(t *testing.T) {
		retrievedUser, err := userStore.Get(ctx, user.UserID)
		assert.NoError(t, err)
		assert.Equal(t, user.UserID, retrievedUser.UserID)
		assert.Equal(t, user.Metadata, retrievedUser.Metadata)
	})

	t.Run("Get Non-Existant Session should result in NotFoundError", func(t *testing.T) {
		_, err := userStore.Get(ctx, "non-existant-user-id")
		assert.ErrorIs(t, err, models.ErrNotFound)
	})

	// Test Update
	t.Run("Update", func(t *testing.T) {
		userUpdate := &models.UpdateUserRequest{
			UserID: user.UserID,
			Metadata: map[string]interface{}{
				"key": "newValue",
			},
		}
		err := userStore.Update(ctx, userUpdate)
		assert.NoError(t, err)

		updatedUser, err := userStore.Get(ctx, user.UserID)
		assert.NoError(t, err)
		assert.Equal(t, userUpdate.Metadata, updatedUser.Metadata)
	})

	t.Run("Update Non-Existant Session should result in NotFoundError", func(t *testing.T) {
		userUpdate := &models.UpdateUserRequest{
			UserID: "non-existant-user-id",
		}
		err := userStore.Update(ctx, userUpdate)
		assert.ErrorIs(t, err, models.ErrNotFound)
	})

	// Test Delete
	t.Run("Delete", func(t *testing.T) {
		err := userStore.Delete(ctx, user.UserID)
		assert.NoError(t, err)

		_, err = userStore.Get(ctx, user.UserID)
		assert.ErrorIs(t, err, models.ErrNotFound)
	})

	t.Run("Delete Non-Existant Session should result in NotFoundError", func(t *testing.T) {
		err := userStore.Delete(ctx, "non-existant-user-id")
		assert.ErrorIs(t, err, models.ErrNotFound)
	})

	// Test GetSessions
	t.Run("GetSessions", func(t *testing.T) {
		_, err := userStore.Create(ctx, user)
		assert.NoError(t, err)

		returnedUser, err := userStore.Get(ctx, user.UserID)
		assert.NoError(t, err)

		// Create some sessions for the user
		session1, err := testutils.GenerateRandomSessionID(16)
		assert.NoError(t, err)
		session2, err := testutils.GenerateRandomSessionID(16)
		assert.NoError(t, err)
		sessionIDs := []string{session1, session2}
		metadataValues := []string{"value1", "value2"}

		sessionStore := NewSessionDAO(testDB)

		for i := 0; i < 2; i++ {
			session := &models.CreateSessionRequest{
				SessionID: sessionIDs[i],
				Metadata: map[string]interface{}{
					"key": metadataValues[i],
				},
				UserUUID: &returnedUser.UUID,
			}
			_, err = sessionStore.Create(ctx, session)
			assert.NoError(t, err)
		}

		// Retrieve the sessions
		sessions, err := userStore.GetSessions(ctx, user.UserID)
		assert.NoError(t, err)

		// Check the returned sessions
		assert.Equal(t, 2, len(sessions))
		assert.ElementsMatch(t, sessionIDs, []string{sessions[0].SessionID, sessions[1].SessionID})
	})
}
