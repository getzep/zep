package postgres

import (
	"context"
	"strings"
	"time"

	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

var _ models.UserStore = &UserStoreDAO{}

type UserStoreDAO struct {
	db *bun.DB
}

func NewUserStoreDAO(db *bun.DB) *UserStoreDAO {
	return &UserStoreDAO{
		db: db,
	}
}

func (dao *UserStoreDAO) Create(
	ctx context.Context,
	user *models.CreateUserRequest,
) (uuid.UUID, error) {
	userDB := UserSchema{
		UserID:   user.UserID,
		Metadata: user.Metadata,
	}
	_, err := dao.db.NewInsert().Model(&userDB).Returning("uuid").Exec(ctx)
	return userDB.UUID, err
}

func (dao *UserStoreDAO) Get(ctx context.Context, userID string) (*models.User, error) {
	user := new(UserSchema)
	err := dao.db.NewSelect().Model(user).Where("user_id = ?", userID).Scan(ctx)
	if err != nil {
		if strings.Contains(err.Error(), "no rows in result set") {
			return nil, models.NewNotFoundError("user " + userID)
		}
		return nil, err
	}
	return userSchemaToUser(user), nil
}

func (dao *UserStoreDAO) Update(ctx context.Context, user *models.UpdateUserRequest) error {
	userDB := UserSchema{
		Metadata: user.Metadata,
	}
	r, err := dao.db.NewUpdate().
		Model(&userDB).
		Column("metadata").
		Where("user_id = ?", user.UserID).
		Exec(ctx)
	if err != nil {
		return err
	}
	rowsAffected, err := r.RowsAffected()
	if err != nil {
		return err
	}
	if rowsAffected == 0 {
		return models.NewNotFoundError("user " + user.UserID)
	}

	return nil
}

func (dao *UserStoreDAO) Delete(ctx context.Context, userID string) error {
	r, err := dao.db.NewDelete().Model(&models.User{}).Where("user_id = ?", userID).Exec(ctx)
	if err != nil {
		return err
	}
	rowsAffected, err := r.RowsAffected()
	if err != nil {
		return err
	}
	if rowsAffected == 0 {
		return models.NewNotFoundError("user " + userID)
	}

	return nil
}

func (dao *UserStoreDAO) ListAll(
	ctx context.Context,
	cursor time.Time,
	limit int,
) ([]*models.User, error) {
	var usersDB []*UserSchema
	err := dao.db.NewSelect().
		Model(&usersDB).
		Where("created_at > ?", cursor).
		OrderExpr("created_at ASC").
		Limit(limit).
		Scan(ctx)
	if err != nil {
		return nil, err
	}

	users := make([]*models.User, len(usersDB))
	for i := range users {
		users[i] = userSchemaToUser(usersDB[i])
	}

	return users, nil
}

func (dao *UserStoreDAO) GetSessions(
	ctx context.Context,
	userID string,
) ([]*models.Session, error) {
	var sessionsDB []*SessionSchema
	err := dao.db.NewSelect().
		Model(&sessionsDB).
		Join("JOIN users u ON u.uuid = s.user_uuid").
		Where("u.user_id = ?", userID).
		Scan(ctx)
	if err != nil {
		return nil, err
	}

	sessions := make([]*models.Session, len(sessionsDB))
	for i := range sessions {
		sessions[i] = &models.Session{
			UUID:      sessionsDB[i].UUID,
			CreatedAt: sessionsDB[i].CreatedAt,
			UpdatedAt: sessionsDB[i].UpdatedAt,
			SessionID: sessionsDB[i].SessionID,
			Metadata:  sessionsDB[i].Metadata,
		}
	}
	return sessions, nil
}

func userSchemaToUser(user *UserSchema) *models.User {
	return &models.User{
		UUID:      user.UUID,
		CreatedAt: user.CreatedAt,
		UpdatedAt: user.UpdatedAt,
		UserID:    user.UserID,
		Metadata:  user.Metadata,
	}
}
