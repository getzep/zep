
package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/uptrace/bun"

	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

func sessionSchemaExt(data ...*models.CreateSessionRequest) SessionSchemaExt {
	return SessionSchemaExt{}
}

func (dao *sessionDAO) buildUpdate(ctx context.Context, session *models.UpdateSessionRequest) (SessionSchema, []string) {
	return dao._buildUpdate(ctx, session)
}

func (dao *sessionDAO) sessionRelations(q *bun.SelectQuery) {}

func (dao *sessionDAO) cleanup(ctx context.Context, sessionID string, tx bun.Tx) error {
	return nil
}

func (dao *sessionDAO) Get(ctx context.Context, sessionID string) (*models.Session, error) {
	session, err := dao.getBySessionID(ctx, sessionID, false)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, zerrors.NewNotFoundError("session " + sessionID)
		}
		return nil, fmt.Errorf("sessionDAO Get failed to get session: %w", err)
	}

	resp := sessionSchemaToSession(*session)[0]

	return resp, nil
}

func sessionSchemaToSession(sessions ...SessionSchema) []*models.Session {
	retSessions := make([]*models.Session, len(sessions))
	for i, sess := range sessions {
		s := _sessionSchemaToSession(sess)

		retSessions[i] = s
	}
	return retSessions
}
