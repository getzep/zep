
package store

import (
	"context"
	"fmt"

	"github.com/getzep/zep/lib/graphiti"
	"github.com/getzep/zep/models"
)

func (us *userStore) _processCreatedUser(ctx context.Context, user *models.User) error {
	err := graphiti.I().AddNode(ctx, graphiti.AddNodeRequest{
		GroupID: user.UserID,
		UUID:    user.UserID,
		Name:    fmt.Sprintf("User %s %s", user.FirstName, user.LastName),
		Summary: fmt.Sprintf("User %s %s", user.FirstName, user.LastName),
	})
	return err
}

func (us *userStore) _cleanupDeletedUser(ctx context.Context, userID string, sessionIDs []string) error {
	err := graphiti.I().DeleteGroup(ctx, userID)
	if err != nil {
		return err
	}
	for _, sessionID := range sessionIDs {
		err := graphiti.I().DeleteGroup(ctx, sessionID)
		if err != nil {
			return err
		}
	}
	return purgeDeletedResources(ctx, us.as.DB)
}
