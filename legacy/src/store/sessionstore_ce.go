
package store

import (
	"context"
	"errors"
	"fmt"

	"github.com/getzep/zep/lib/graphiti"
)

func (dao *sessionDAO) _cleanupDeletedSession(ctx context.Context) error {
	return purgeDeletedResources(ctx, dao.as.DB)
}

func (dao *sessionDAO) _postCreateSession(ctx context.Context, sessionID, userID string) error {
	user, err := dao.rs.Users.Get(ctx, userID)
	if err != nil {
		return fmt.Errorf("failed to get user: %w", err)
	}
	if user == nil {
		return errors.New("user not found")
	}
	name := fmt.Sprintf("User %s %s", user.FirstName, user.LastName)
	return graphiti.I().AddNode(ctx, graphiti.AddNodeRequest{
		GroupID: sessionID,
		UUID:    fmt.Sprintf("%s_%s", sessionID, userID),
		Name:    name,
		Summary: name,
	})
}
