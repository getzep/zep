package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"sync"

	"github.com/google/uuid"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/lib/enablement"
	"github.com/getzep/zep/lib/logger"
	"github.com/getzep/zep/lib/pg"
	"github.com/getzep/zep/lib/telemetry"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

func NewUserStore(as *models.AppState, rs *models.RequestState) models.UserStore {
	return &userStore{
		as: as,
		rs: rs,
	}
}

type userStore struct {
	as *models.AppState
	rs *models.RequestState
}

func (us *userStore) Create(ctx context.Context, data *models.CreateUserRequest) (*models.User, error) {
	if data.UserID == "" {
		return nil, zerrors.NewBadRequestError("UserID cannot be empty")
	}

	// TODO do we need to do this or can we rely on the database to enforce this?
	// this isn't an error we should be worried about returning to the user as it is
	// really an issue with the code and not something the user can fix.
	if us.rs.ProjectUUID == uuid.Nil {
		return nil, zerrors.NewBadRequestError("ProjectUUID cannot be empty")
	}

	user := UserSchema{
		BaseSchema:  NewBaseSchema(us.rs.SchemaName, "users"),
		UserID:      data.UserID,
		Email:       data.Email,
		FirstName:   data.FirstName,
		LastName:    data.LastName,
		Metadata:    data.Metadata,
		ProjectUUID: us.rs.ProjectUUID,
	}

	_, err := us.as.DB.NewInsert().
		Model(&user).
		ModelTableExpr("?.users AS u", bun.Ident(us.rs.SchemaName)).
		Returning("*").
		Exec(ctx)
	if err != nil {
		if pg.IsIntegrityViolation(err) {
			return nil, zerrors.NewBadRequestError(
				"user already exists with user_id: " + data.UserID,
			)
		}
		return nil, err
	}

	createdUser := userSchemaToUser(&user, 0)

	err = us._processCreatedUser(ctx, createdUser)
	if err != nil {
		return nil, err
	}

	telemetry.I().TrackEvent(us.rs, telemetry.Event_CreateUser, map[string]any{
		"has_email":    user.Email != "",
		"has_metadata": user.Metadata != nil,
	})
	enablement.I().TrackEvent(enablement.Event_CreateUser, us.rs)

	return createdUser, nil
}

func (us *userStore) Get(ctx context.Context, userID string) (*models.User, error) {
	user := UserSchema{
		BaseSchema: NewBaseSchema(us.rs.SchemaName, "users"),
	}

	err := us.as.DB.NewSelect().
		Model(&user).
		ModelTableExpr("?.users AS u", bun.Ident(us.rs.SchemaName)).
		Where("user_id = ?", userID).
		Where("project_uuid = ?", us.rs.ProjectUUID).
		Scan(ctx)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, zerrors.NewNotFoundError("user " + userID)
		}

		return nil, err
	}

	result, err := us.userSchemaToUser(ctx, []UserSchema{user})
	if err != nil {
		return nil, err
	}

	return result[0], nil
}

func (us *userStore) Update(ctx context.Context, user *models.UpdateUserRequest, isPrivileged bool) (*models.User, error) {
	if user.UserID == "" {
		return nil, errors.New("UserID cannot be empty")
	}

	// if metadata is null or empty, we can keep this a cheap operation
	if len(user.Metadata) == 0 {
		return us.updateUser(ctx, user)
	}

	// TODO this seems more expensive than it needs to be. coulnd't this be handled
	// with a transaction?

	// Acquire a lock for this UserID. This is to prevent concurrent updates
	// to the session metadata.
	lockID, err := safelyAcquireMetadataLock(ctx, us.as.DB, user.UserID)
	if err != nil {
		return nil, fmt.Errorf("failed to acquire advisory lock: %w", zerrors.ErrLockAcquisitionFailed)
	}

	defer func(ctx context.Context, db bun.IDB, lockID uint64) {
		err := releaseAdvisoryLock(ctx, db, lockID)
		if err != nil {
			logger.Error("failed to release advisory lock", "error", err)
		}
	}(ctx, us.as.DB, lockID)

	mergeData := mergeMetadataRequest{
		entityField: "user_id",
		entityID:    user.UserID,
		table:       "users",
		metadata:    user.Metadata,
	}

	mergedMetadata, err := mergeMetadata(
		ctx,
		us.as.DB,
		us.rs.SchemaName,
		mergeData,
		isPrivileged,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to merge metadata: %w", err)
	}

	user.Metadata = mergedMetadata

	return us.updateUser(ctx, user)
}

func (us *userStore) updateUser(ctx context.Context, user *models.UpdateUserRequest) (*models.User, error) {
	userDB := &UserSchema{
		BaseSchema: NewBaseSchema(us.rs.SchemaName, "users"),
		Email:      user.Email,
		FirstName:  user.FirstName,
		LastName:   user.LastName,
		Metadata:   user.Metadata,
	}

	r, err := us.as.DB.NewUpdate().
		Model(userDB).
		ModelTableExpr("?.users AS u", bun.Ident(us.rs.SchemaName)).
		Column("email", "first_name", "last_name", "metadata", "updated_at").
		OmitZero().
		Where("user_id = ?", user.UserID).
		Where("project_uuid = ?", us.rs.ProjectUUID).
		Exec(ctx)
	if err != nil {
		return nil, err
	}

	rowsAffected, err := r.RowsAffected()
	if err != nil {
		return nil, err
	}

	if rowsAffected == 0 {
		return nil, zerrors.NewNotFoundError("user " + user.UserID)
	}

	// We're can't return the updated User above as we're using OmitZero,
	// so we need to get the updated user from the DB
	updatedUser, err := us.Get(ctx, user.UserID)
	if err != nil {
		return nil, err
	}

	return updatedUser, nil
}

func (us *userStore) Delete(ctx context.Context, userID string) error {
	tx, err := us.as.DB.Begin()
	if err != nil {
		return err
	}

	defer rollbackOnError(tx)

	sessions, err := us.GetSessionsForUser(ctx, userID)
	if err != nil {
		return err
	}

	var sessionIds []string
	for _, s := range sessions {
		if s != nil && s.SessionID != "" {
			sessionIds = append(sessionIds, s.SessionID)
		}
	}

	for _, s := range sessions {
		err := us.rs.Sessions.Delete(ctx, s.SessionID)
		if err != nil {
			return err
		}
	}

	r, err := us.as.DB.NewDelete().
		Model(&models.User{}).
		ModelTableExpr("?.users AS u", bun.Ident(us.rs.SchemaName)).
		Where("project_uuid = ?", us.rs.ProjectUUID).
		Where("user_id = ?", userID).
		Exec(ctx)
	if err != nil {
		return err
	}

	rowsAffected, err := r.RowsAffected()
	if err != nil {
		return err
	}

	if rowsAffected == 0 {
		return zerrors.NewNotFoundError("user " + userID)
	}

	err = tx.Commit()
	if err != nil {
		return err
	}

	err = us._cleanupDeletedUser(ctx, userID, sessionIds)
	if err != nil {
		return err
	}

	telemetry.I().TrackEvent(us.rs, telemetry.Event_DeleteUser)
	enablement.I().TrackEvent(enablement.Event_DeleteUser, us.rs)

	return nil
}

func (us *userStore) ListAll(ctx context.Context, cursor int64, limit int) ([]*models.User, error) {
	// TODO do we need this or can we rely on the database to enforce this?
	if us.rs.ProjectUUID == uuid.Nil {
		return nil, zerrors.NewBadRequestError("ProjectUUID cannot be empty")
	}

	var users []UserSchema

	err := us.as.DB.NewSelect().
		Model(&users).
		ModelTableExpr("?.users AS u", bun.Ident(us.rs.SchemaName)).
		Where("project_uuid = ?", us.rs.ProjectUUID).
		Where("id > ?", cursor).
		OrderExpr("id ASC").
		Limit(limit).
		Scan(ctx)
	if err != nil {
		return nil, err
	}

	result, err := us.userSchemaToUser(ctx, users)
	if err != nil {
		return nil, err
	}

	return result, nil
}

func (us *userStore) ListAllOrdered(ctx context.Context, pageNumber, pageSize int, orderBy string, asc bool) (*models.UserListResponse, error) {
	var (
		totalCount int
		wg         sync.WaitGroup
		mu         sync.Mutex
		firstErr   error
		users      []UserSchema
	)

	if us.rs.ProjectUUID == uuid.Nil {
		return nil, zerrors.NewBadRequestError("ProjectUUID cannot be empty")
	}

	if orderBy == "" {
		orderBy = "id"
	}

	direction := "DESC"
	if asc {
		direction = "ASC"
	}

	wg.Add(1)
	go func() {
		defer wg.Done()

		err := us.as.DB.NewSelect().
			Model(&users).
			ModelTableExpr("?.users AS u", bun.Ident(us.rs.SchemaName)).
			Where("project_uuid = ?", us.rs.ProjectUUID).
			Order(fmt.Sprintf("%s %s", orderBy, direction)).
			Limit(pageSize).
			Offset((pageNumber - 1) * pageSize).
			Scan(ctx)

		mu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		mu.Unlock()
	}()

	wg.Add(1)
	go func() {
		defer wg.Done()

		var err error

		totalCount, err = us.as.DB.NewSelect().
			Model((*UserSchema)(nil)).
			ModelTableExpr("?.users AS u", bun.Ident(us.rs.SchemaName)).
			Where("u.project_uuid = ?", us.rs.ProjectUUID).
			Count(ctx)

		mu.Lock()
		if firstErr == nil {
			firstErr = err
		}
		mu.Unlock()
	}()

	wg.Wait()

	if firstErr != nil {
		return nil, fmt.Errorf("failed to list users: %w", firstErr)
	}

	result, err := us.userSchemaToUser(ctx, users)
	if err != nil {
		return nil, err
	}

	return &models.UserListResponse{
		Users:      result,
		RowCount:   len(result),
		TotalCount: totalCount,
	}, nil
}

func (us *userStore) GetSessionsForUser(ctx context.Context, userID string) ([]*models.Session, error) {
	var sessions []*SessionSchema

	err := us.as.DB.NewSelect().
		Model(&sessions).
		ModelTableExpr("?.sessions AS s", bun.Ident(us.rs.SchemaName)).
		Where("s.project_uuid = ?", us.rs.ProjectUUID).
		Join("JOIN ?.users u ON u.user_id = s.user_id", bun.Ident(us.rs.SchemaName)).
		Where("u.user_id = ?", userID).
		Scan(ctx)
	if err != nil {
		return nil, err
	}

	result := make([]*models.Session, len(sessions))
	for i, s := range sessions {
		result[i] = &models.Session{
			SessionCommon: models.SessionCommon{
				UUID:      s.UUID,
				CreatedAt: s.CreatedAt,
				UpdatedAt: s.UpdatedAt,
				SessionID: s.SessionID,
				Metadata:  s.Metadata,
				UserID:    s.UserID,
			},
		}
	}

	return result, nil
}

func (us *userStore) userSchemaToUser(ctx context.Context, users []UserSchema) ([]*models.User, error) {
	result := make([]*models.User, len(users))
	for i, u := range users {
		_u := u

		sessions, err := us.GetSessionsForUser(ctx, _u.UserID)
		if err != nil {
			return nil, err
		}

		result[i] = userSchemaToUser(&_u, len(sessions))
	}

	return result, nil
}

func userSchemaToUser(user *UserSchema, sessionCount int) *models.User {
	return &models.User{
		UUID:         user.UUID,
		ID:           user.ID,
		ProjectUUID:  user.ProjectUUID,
		CreatedAt:    user.CreatedAt,
		UpdatedAt:    user.UpdatedAt,
		UserID:       user.UserID,
		Email:        user.Email,
		FirstName:    user.FirstName,
		LastName:     user.LastName,
		Metadata:     user.Metadata,
		SessionCount: sessionCount,
	}
}
