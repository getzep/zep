package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"slices"
	"sync"

	"github.com/google/uuid"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/lib/logger"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/getzep/zep/models"
)

func newMessageDAO(as *models.AppState, rs *models.RequestState, sessionID string) *messageDAO {
	return &messageDAO{
		as:        as,
		rs:        rs,
		sessionID: sessionID,
	}
}

type messageDAO struct {
	as        *models.AppState
	rs        *models.RequestState
	sessionID string
}

func (dao *messageDAO) Create(ctx context.Context, message *models.Message) (*models.Message, error) {
	// Create a new MessageStoreSchema from the provided message
	pgMessage := MessageStoreSchema{
		UUID:        message.UUID,
		SessionID:   dao.sessionID,
		ProjectUUID: dao.rs.ProjectUUID,
		Role:        message.Role,
		RoleType:    message.RoleType,
		Content:     message.Content,
		TokenCount:  message.TokenCount,
		Metadata:    message.Metadata,
		BaseSchema:  NewBaseSchema(dao.rs.SchemaName, "messages"),
	}

	// Insert the new message into the database
	_, err := dao.as.DB.NewInsert().
		Model(&pgMessage).
		ModelTableExpr("?", bun.Ident(pgMessage.GetTableName())).
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to create message: %w", err)
	}

	return &models.Message{
		UUID:       pgMessage.UUID,
		CreatedAt:  pgMessage.CreatedAt,
		UpdatedAt:  pgMessage.UpdatedAt,
		Role:       pgMessage.Role,
		Content:    pgMessage.Content,
		TokenCount: pgMessage.TokenCount,
		Metadata:   pgMessage.Metadata,
	}, nil
}

func (dao *messageDAO) CreateMany(ctx context.Context, messages []models.Message) ([]models.Message, error) {
	if len(messages) == 0 {
		return nil, nil
	}

	pgMessages := make([]MessageStoreSchema, len(messages))
	for i := range messages {
		pgMessages[i] = MessageStoreSchema{
			UUID:        messages[i].UUID,
			SessionID:   dao.sessionID,
			ProjectUUID: dao.rs.ProjectUUID,
			Role:        messages[i].Role,
			RoleType:    messages[i].RoleType, Content: messages[i].Content,
			TokenCount: messages[i].TokenCount,
			Metadata:   messages[i].Metadata,
			BaseSchema: NewBaseSchema(dao.rs.SchemaName, "messages"),
		}
	}

	_, err := dao.as.DB.NewInsert().
		Model(&pgMessages).
		ModelTableExpr("? as m", bun.Ident(pgMessages[0].GetTableName())).
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, zerrors.CheckForIntegrityViolationError(
			err,
			"message_uuid already exists",
			"failed to create messages",
		)
	}

	messages = messagesFromStoreSchema(pgMessages)

	return messages, nil
}

func (dao *messageDAO) Get(ctx context.Context, messageUUID uuid.UUID) (*models.Message, error) {
	messages := &MessageStoreSchema{
		BaseSchema: NewBaseSchema(dao.rs.SchemaName, "messages"),
	}
	err := dao.as.DB.NewSelect().
		Model(messages).
		ModelTableExpr("?.messages as m", bun.Ident(dao.rs.SchemaName)).
		Where("m.session_id = ?", dao.sessionID).
		Where("m.project_uuid = ?", dao.rs.ProjectUUID).
		Where("m.uuid = ?", messageUUID).
		Scan(ctx)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, zerrors.NewNotFoundError(fmt.Sprintf("message %s not found", messageUUID))
		}
		return nil, fmt.Errorf("unable to retrieve messages %w", err)
	}

	return &models.Message{
		UUID:       messages.UUID,
		Role:       messages.Role,
		Content:    messages.Content,
		TokenCount: messages.TokenCount,
		Metadata:   messages.Metadata,
	}, nil
}

// GetLastN retrieves the last N messages for a session. if lastN us 0, no limit is applied.
// If uuid is provided, it will get the last N messages before and including the provided beforeUUID.
// Results are returned in ascending order of creation
func (dao *messageDAO) GetLastN(ctx context.Context, lastN int, beforeUUID uuid.UUID) ([]models.Message, error) {
	var (
		index int64
		err   error
	)

	if beforeUUID != uuid.Nil {
		// Get the index of the message with the provided UUID
		index, err = getMessageIndex(ctx, dao.as, dao.rs, dao.sessionID, beforeUUID)
	}

	if err != nil {
		return nil, fmt.Errorf("unable to retrieve message index %w", err)
	}

	var messagesDB []MessageStoreSchema

	// Expected to use memstore_session_id_project_uuid_deleted_at_idx. Do not change the order of the where clauses.
	query := dao.as.DB.NewSelect().
		Model(&messagesDB).
		ModelTableExpr("?.messages as m", bun.Ident(dao.rs.SchemaName)).
		Where("session_id = ?", dao.sessionID).
		Where("project_uuid = ?", dao.rs.ProjectUUID)

	// If beforeUUID is provided, get the last N messages before and including the provided UUID
	if beforeUUID != uuid.Nil {
		query = query.Where("id <= ?", index)
	}

	query = query.Order("id DESC")

	// If lastN is provided, limit the query to the last N messages
	if lastN > 0 {
		query = query.Limit(lastN)
	}

	err = query.Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("unable to retrieve messages %w", err)
	}

	// Reverse the slice so that the messages are in ascending order
	if len(messagesDB) > 0 {
		slices.Reverse(messagesDB)
	}

	messages := messagesFromStoreSchema(messagesDB)

	return messages, nil
}

// GetListByUUID retrieves a list of messages by their UUIDs.
// Does not reorder the messages.
func (dao *messageDAO) GetListByUUID(ctx context.Context, messageUUIDs []uuid.UUID) ([]models.Message, error) {
	if len(messageUUIDs) == 0 {
		return []models.Message{}, nil
	}

	var messages []MessageStoreSchema
	err := dao.as.DB.NewSelect().
		Model(&messages).
		ModelTableExpr("?.messages as m", bun.Ident(dao.rs.SchemaName)).
		Where("session_id = ?", dao.sessionID).
		Where("project_uuid = ?", dao.rs.ProjectUUID).
		Where("uuid IN (?)", bun.In(messageUUIDs)).
		Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("unable to retrieve messages %w", err)
	}

	messageList := messagesFromStoreSchema(messages)

	return messageList, nil
}

// GetListBySession retrieves a list of messages for a session. The list is paginated.
func (dao *messageDAO) GetListBySession(ctx context.Context, currentPage, pageSize int) (*models.MessageListResponse, error) {
	var (
		wg       sync.WaitGroup
		countErr error
		count    int
	)

	wg.Add(1)
	go func() {
		defer wg.Done()
		messageSchemaStore := &MessageStoreSchema{
			BaseSchema: NewBaseSchema(dao.rs.SchemaName, "messages"),
		}
		// Get count of all messages for this session
		count, countErr = dao.as.DB.NewSelect().
			Model(messageSchemaStore).
			ModelTableExpr("? as m", bun.Ident(messageSchemaStore.GetTableName())).
			Where("m.session_id = ?", dao.sessionID).
			Where("m.project_uuid = ?", dao.rs.ProjectUUID).
			Count(ctx)
	}()

	var messages []MessageStoreSchema
	err := dao.as.DB.NewSelect().
		Model(&messages).
		ModelTableExpr("?.messages as m", bun.Ident(dao.rs.SchemaName)).
		Where("m.session_id = ?", dao.sessionID).
		Where("m.project_uuid = ?", dao.rs.ProjectUUID).
		OrderExpr("id ASC").
		Limit(pageSize).
		Offset((currentPage - 1) * pageSize).
		Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get messages %w", err)
	}
	if len(messages) == 0 {
		return &models.MessageListResponse{
			Messages:   []models.Message{},
			TotalCount: 0,
			RowCount:   0,
		}, nil
	}

	messageList := make([]models.Message, len(messages))
	for i := range messages {
		messageList[i] = models.Message{
			UUID:       messages[i].UUID,
			CreatedAt:  messages[i].CreatedAt,
			Role:       messages[i].Role,
			RoleType:   messages[i].RoleType,
			Content:    messages[i].Content,
			TokenCount: messages[i].TokenCount,
			Metadata:   messages[i].Metadata,
		}
	}

	wg.Wait()
	if countErr != nil {
		return nil, fmt.Errorf("failed to get message count %w", countErr)
	}

	return &models.MessageListResponse{
		Messages:   messageList,
		TotalCount: count,
		RowCount:   len(messages),
	}, nil
}

// Update updates a message by its UUID. Metadata is updated via a merge.
// If includeContent is true, the content and role fields are updated, too.
func (dao *messageDAO) Update(ctx context.Context, message *models.Message, includeContent, isPrivileged bool) error {
	if message.UUID == uuid.Nil {
		return fmt.Errorf("message UUID cannot be nil")
	}

	// Don't update the Metadata field here. We do this via a merge below.
	messageDB := MessageStoreSchema{
		Role:       message.Role,
		Content:    message.Content,
		TokenCount: message.TokenCount,
		BaseSchema: NewBaseSchema(dao.rs.SchemaName, "messages"),
	}

	columns := []string{"token_count"}
	if includeContent {
		columns = append(columns, "role", "content")
	}

	// we're intentionally not running in a TX here to reduce complexity
	// if the metadata update fails, the message update will still be committed
	db := dao.as.DB
	r, err := db.NewUpdate().
		Model(&messageDB).
		ModelTableExpr("? as m", bun.Ident(messageDB.GetTableName())).
		Column(columns...).
		Where("m.session_id = ?", dao.sessionID).
		Where("m.project_uuid = ?", dao.rs.ProjectUUID).
		Where("m.uuid = ?", message.UUID).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to update message: %w", err)
	}

	rows, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get affected rows: %w", err)
	}
	if rows == 0 {
		return zerrors.NewNotFoundError(fmt.Sprintf("message %s not found", message.UUID))
	}

	// Update metadata
	if len(message.Metadata) != 0 {
		err = dao.updateMetadata(ctx, db, message.UUID, message.Metadata, isPrivileged)
		if err != nil {
			return fmt.Errorf("failed to update message metadata: %w", err)
		}
	}

	return nil
}

func (dao *messageDAO) UpdateMany(ctx context.Context, messages []models.Message, includeContent, isPrivileged bool) error {
	if len(messages) == 0 {
		return nil
	}

	messagesDB := make([]MessageStoreSchema, len(messages))
	for i := range messages {
		if messages[i].UUID == uuid.Nil {
			return fmt.Errorf("message UUID cannot be nil")
		}
		messagesDB[i] = MessageStoreSchema{
			UUID:       messages[i].UUID,
			Role:       messages[i].Role,
			RoleType:   messages[i].RoleType,
			Content:    messages[i].Content,
			TokenCount: messages[i].TokenCount,
			BaseSchema: NewBaseSchema(dao.rs.SchemaName, "messages"),
		}
	}

	updatedValues := dao.as.DB.NewValues(&messagesDB)

	db := dao.as.DB
	query := db.NewUpdate().
		With("_data", updatedValues).
		Model(&messagesDB).
		ModelTableExpr("? as m", bun.Ident(messagesDB[0].GetTableName())).
		Where("m.project_uuid = ?", dao.rs.ProjectUUID).
		TableExpr("_data").
		Set("token_count = _data.token_count")

	if includeContent {
		query = query.Set("role = _data.role").
			Set("content = _data.content")
	}

	_, err := query.
		Where("m.uuid = _data.uuid").
		Where("m.session_id = ?", dao.sessionID).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to update messages: %w", err)
	}

	// Update metadata
	// we're intentionally not running in a TX here to reduce complexity
	// if the metadata update fails, the message update will still be committed
	for i := range messages {
		if len(messages[i].Metadata) != 0 {
			err = dao.updateMetadata(ctx, db, messages[i].UUID, messages[i].Metadata, isPrivileged)
			if err != nil {
				return fmt.Errorf("failed to update message metadata: %w", err)
			}
		}
	}

	return nil
}

// updateMetadata updates the metadata for a message by its UUID. Metadata is updated via a merge.
// An advisory lock is acquired on the message UUID to prevent concurrent updates to the metadata.
func (dao *messageDAO) updateMetadata(
	ctx context.Context,
	tx bun.IDB, // use bun.IDB interface to make it easier to test
	messageUUID uuid.UUID,
	metadata map[string]any,
	isPrivileged bool,
) error {
	// Acquire a lock for this Message UUID. This is to prevent concurrent updates
	// to the message metadata.
	lockID, err := safelyAcquireMetadataLock(ctx, dao.as.DB, messageUUID.String())
	if err != nil {
		return fmt.Errorf("failed to acquire advisory lock: %w", zerrors.ErrLockAcquisitionFailed)
	}

	defer func(ctx context.Context, db bun.IDB, lockID uint64) {
		err := releaseAdvisoryLock(ctx, db, lockID)
		if err != nil {
			logger.Error("failed to release advisory lock", "error", err)
		}
	}(ctx, tx, lockID)

	mergeData := mergeMetadataRequest{
		entityField: "uuid",
		entityID:    messageUUID.String(),
		table:       "messages",
		metadata:    metadata,
	}

	mergedMetadata, err := mergeMetadata(
		ctx,
		tx,
		dao.rs.SchemaName,
		mergeData,
		isPrivileged,
	)
	if err != nil {
		return fmt.Errorf("failed to merge message metadata: %w", err)
	}

	messageStoreSchema := &MessageStoreSchema{
		BaseSchema: NewBaseSchema(dao.rs.SchemaName, "messages"),
	}

	_, err = tx.NewUpdate().
		Model(messageStoreSchema).
		ModelTableExpr("? as m", bun.Safe(messageStoreSchema.GetTableName())).
		Column("metadata").
		Where("m.session_id = ?", dao.sessionID).
		Where("m.uuid = ?", messageUUID).
		Where("m.project_uuid = ?", dao.rs.ProjectUUID).
		Set("metadata = ?", mergedMetadata).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to update message metadata: %w", err)
	}

	return nil
}

func (dao *messageDAO) Delete(ctx context.Context, messageUUID uuid.UUID) error {
	if messageUUID == uuid.Nil {
		return fmt.Errorf("message UUID cannot be nil")
	}

	tx, err := dao.as.DB.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	err = dao.cleanup(ctx, messageUUID, &tx)
	if err != nil {
		return fmt.Errorf("failed to cleanup message: %w", err)
	}

	messageStoreSchema := &MessageStoreSchema{
		BaseSchema: NewBaseSchema(dao.rs.SchemaName, "messages"),
	}
	// Delete the message
	r, err := tx.NewDelete().
		Model(messageStoreSchema).
		ModelTableExpr("? as m", bun.Ident(messageStoreSchema.GetTableName())).
		Where("session_id = ?", dao.sessionID).
		Where("project_uuid = ?", dao.rs.ProjectUUID).
		Where("uuid = ?", messageUUID).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to delete message: %w", err)
	}

	rows, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get affected rows: %w", err)
	}

	if rows == 0 {
		return zerrors.NewNotFoundError(fmt.Sprintf("message %s not found", messageUUID))
	}

	err = tx.Commit()
	if err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}
	return nil
}

// getMessageIndex retrieves the index of the message with the provided UUID.
// If the messageUUID does not exist (for e.g. if it was deleted), returns 0.
func getMessageIndex(
	ctx context.Context,
	as *models.AppState,
	requestState *models.RequestState,
	sessionID string,
	messageUUID uuid.UUID,
) (int64, error) {
	message := MessageStoreSchema{
		BaseSchema: NewBaseSchema(requestState.SchemaName, "messages"),
	}

	// Expected to use memstore_session_id_project_uuid_deleted_at_idx. Do not change the order of the where clauses.
	err := as.DB.NewSelect().
		Model(&message).
		ModelTableExpr("? as m", bun.Ident(message.GetTableName())).
		Column("id").
		Where("m.session_id = ? AND m.uuid = ?", sessionID, messageUUID).
		Where("m.project_uuid = ?", requestState.ProjectUUID).
		Scan(ctx)
	if err != nil {
		if !errors.Is(err, sql.ErrNoRows) {
			return 0, err
		}

		return 0, nil
	}

	return message.ID, nil
}

func messagesFromStoreSchema(messages []MessageStoreSchema) []models.Message {
	messageList := make([]models.Message, len(messages))
	for i := range messages {
		messageList[i] = models.Message{
			UUID:       messages[i].UUID,
			CreatedAt:  messages[i].CreatedAt,
			Role:       messages[i].Role,
			RoleType:   messages[i].RoleType,
			Content:    messages[i].Content,
			TokenCount: messages[i].TokenCount,
			Metadata:   messages[i].Metadata,
		}
	}
	return messageList
}
