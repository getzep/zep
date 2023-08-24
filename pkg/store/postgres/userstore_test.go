package postgres

import (
	"context"
	"testing"
	"time"

	"github.com/getzep/zep/pkg/testutils"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
)

func TestUserStoreDAO(t *testing.T) {
	ctx := context.Background()

	userID := testutils.GenerateRandomString(16)

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

	t.Run("Update should not overwrite with zero values", func(t *testing.T) {
		userID := testutils.GenerateRandomString(16)
		// Create a user with non-zero values
		user := &models.CreateUserRequest{
			UserID: userID,
			Metadata: map[string]interface{}{
				"key": "value",
			},
			Email: "email",
		}
		_, err := userStore.Create(ctx, user)
		assert.NoError(t, err)

		// Update the user with zero values
		userUpdate := &models.UpdateUserRequest{
			UserID:    user.UserID,
			Metadata:  nil,
			Email:     "",
			FirstName: "bob",
		}
		err = userStore.Update(ctx, userUpdate)
		assert.NoError(t, err)

		// Retrieve the updated user
		updatedUser, err := userStore.Get(ctx, user.UserID)
		assert.NoError(t, err)

		// Check that the updated user still has the original non-zero values
		assert.Equal(t, user.Metadata, updatedUser.Metadata)
		assert.Equal(t, user.Email, updatedUser.Email)
		assert.Equal(t, "bob", updatedUser.FirstName)
	})

	t.Run("Update Non-Existant Session should result in NotFoundError", func(t *testing.T) {
		userUpdate := &models.UpdateUserRequest{
			UserID: "non-existant-user-id",
			Email:  "email",
		}
		err := userStore.Update(ctx, userUpdate)
		assert.ErrorIs(t, err, models.ErrNotFound)
	})

	// Test GetSessions
	t.Run("GetSessions", func(t *testing.T) {
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

}

func TestUserStoreDAO_ListAll(t *testing.T) {
	CleanDB(t, testDB)
	err := ensurePostgresSetup(testCtx, appState, testDB)
	assert.NoError(t, err)

	// Initialize UserStoreDAO
	dao := NewUserStoreDAO(testDB)

	// Create a few test users
	for i := 0; i < 5; i++ {
		userID := testutils.GenerateRandomString(16)
		assert.NoError(t, err, "GenerateRandomString should not return an error")

		user := &models.CreateUserRequest{
			UserID: userID,
			Metadata: map[string]interface{}{
				"key": "value",
			},
		}
		_, err = dao.Create(testCtx, user)
		assert.NoError(t, err)

		// Update the CreatedAt field to a time relative to now
		_, err = dao.db.NewUpdate().
			Model(&UserSchema{}).
			Set("created_at = ?", time.Now().Add(-time.Duration(i)*time.Hour)).
			Where("user_id = ?", userID).
			Exec(testCtx)
		assert.NoError(t, err)
	}

	tests := []struct {
		name   string
		cursor time.Time
		limit  int
		want   int
	}{
		{
			name:   "Get all users",
			cursor: time.Now().Add(-5 * time.Hour), // 5 hours ago
			limit:  10,
			want:   5,
		},
		{
			name:   "Get users last 2 hours",
			cursor: time.Now().Add(-2 * time.Hour), // 2 hours ago
			limit:  10,
			want:   2,
		},
		{
			name:   "Get no users",
			cursor: time.Now().Add(time.Hour), // 1 hour in the future
			limit:  10,
			want:   0,
		},
		{
			name:   "Limit number of users",
			cursor: time.Now().Add(-5 * time.Hour), // all users
			limit:  3,
			want:   3,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			users, err := dao.ListAll(testCtx, tt.cursor, tt.limit)
			assert.NoError(t, err)
			assert.Equal(t, tt.want, len(users))
		})
	}
}
