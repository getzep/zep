package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"sync"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/store"
	"github.com/pgvector/pgvector-go"

	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

type MessageDAO struct {
	db        *bun.DB
	appState  *models.AppState
	sessionID string
}

func NewMessageDAO(db *bun.DB, appState *models.AppState, sessionID string) (*MessageDAO, error) {
	if db == nil {
		return nil, errors.New("db cannot be nil")
	}
	if appState == nil {
		return nil, errors.New("appState cannot be nil")
	}
	if sessionID == "" {
		return nil, errors.New("sessionID cannot be empty")
	}
	return &MessageDAO{
		db:        db,
		appState:  appState,
		sessionID: sessionID,
	}, nil
}

// Create creates a new message for a session. Create does not create a session if it does not exist.
func (dao *MessageDAO) Create(
	ctx context.Context,
	message *models.Message,
) (*models.Message, error) {
	// Create a new MessageStoreSchema from the provided message
	pgMessage := MessageStoreSchema{
		UUID:       message.UUID,
		SessionID:  dao.sessionID,
		Role:       message.Role,
		Content:    message.Content,
		TokenCount: message.TokenCount,
		Metadata:   message.Metadata,
	}

	// Insert the new message into the database
	_, err := dao.db.NewInsert().
		Model(&pgMessage).
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

// CreateMany creates a batch of messages for a session.
func (dao *MessageDAO) CreateMany(
	ctx context.Context,
	messages []models.Message,
) ([]models.Message, error) {
	if len(messages) == 0 {
		return nil, nil
	}

	pgMessages := make([]MessageStoreSchema, len(messages))
	for i, msg := range messages {
		pgMessages[i] = MessageStoreSchema{
			UUID:       msg.UUID,
			SessionID:  dao.sessionID,
			Role:       msg.Role,
			Content:    msg.Content,
			TokenCount: msg.TokenCount,
			Metadata:   msg.Metadata,
		}
	}

	_, err := dao.db.NewInsert().
		Model(&pgMessages).
		Returning("*").
		Exec(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to create messages %w", err)
	}

	messages = messagesFromStoreSchema(pgMessages)

	return messages, nil
}

// Get retrieves a message by its UUID.
func (dao *MessageDAO) Get(ctx context.Context, messageUUID uuid.UUID) (*models.Message, error) {
	var messages MessageStoreSchema
	err := dao.db.NewSelect().
		Model(&messages).
		Where("session_id = ?", dao.sessionID).
		Where("uuid = ?", messageUUID).
		Scan(ctx)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, models.NewNotFoundError(fmt.Sprintf("message %s not found", messageUUID))
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

// GetLastN retrieves the last N messages for a session. If uuid is provided, it will get the
// last N messages before and including the provided beforeUUID. Results are returned in
// ascending order of creation
func (dao *MessageDAO) GetLastN(
	ctx context.Context,
	lastNMessages int,
	beforeUUID uuid.UUID,
) ([]models.Message, error) {
	var index int64
	var err error
	if beforeUUID != uuid.Nil {
		// Get the index of the message with the provided UUID
		index, err = getMessageIndex(ctx, dao.db, dao.sessionID, beforeUUID)
		if err != nil {
			return nil, fmt.Errorf("unable to retrieve summary point index %w", err)
		}
	}

	var messagesDB []MessageStoreSchema
	query := dao.db.NewSelect().
		Model(&messagesDB).
		Where("session_id = ?", dao.sessionID)

	// If beforeUUID is provided, get the last N messages before and including the provided UUID
	if beforeUUID != uuid.Nil {
		query = query.Where("id <= ?", index)
	}

	query = query.Order("id DESC")

	// If lastNMessages is provided, limit the query to the last N messages
	if lastNMessages > 0 {
		query = query.Limit(lastNMessages)
	}

	err = query.Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("unable to retrieve messages %w", err)
	}

	// Reverse the slice so that the messages are in ascending order
	if len(messagesDB) > 0 {
		internal.ReverseSlice(messagesDB)
	}

	messages := messagesFromStoreSchema(messagesDB)

	return messages, err
}

// GetSinceLastSummary retrieves messages since the last summary point, limited by the memory window.
// If there is no last summary point, all messages are returned, limited by the memory window.
// Results are returned in ascending order of creation
func (dao *MessageDAO) GetSinceLastSummary(
	ctx context.Context,
	lastSummary *models.Summary,
	memoryWindow int,
) ([]models.Message, error) {
	summaryPointUUID := uuid.Nil
	if lastSummary != nil {
		summaryPointUUID = lastSummary.SummaryPointUUID
	}
	// If there is no last summary, returns ID of 0
	lastMessageID, err := getMessageIndex(ctx, dao.db, dao.sessionID, summaryPointUUID)
	if err != nil {
		return nil, fmt.Errorf("unable to retrieve summary point index %w", err)
	}

	var messages []MessageStoreSchema
	err = dao.db.NewSelect().
		Model(&messages).
		Where("session_id = ?", dao.sessionID).
		Where("id > ?", lastMessageID).
		Order("id DESC").
		Limit(memoryWindow).
		Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("unable to retrieve messages %w", err)
	}

	messageList := messagesFromStoreSchema(messages)

	// Reverse the slice so that the messages are in ascending order
	if len(messageList) > 0 {
		internal.ReverseSlice(messageList)
	}

	return messageList, nil
}

// GetListByUUID retrieves a list of messages by their UUIDs.
// Does not reorder the messages.
func (dao *MessageDAO) GetListByUUID(
	ctx context.Context,
	messageUUIDs []uuid.UUID,
) ([]models.Message, error) {
	if len(messageUUIDs) == 0 {
		return []models.Message{}, nil
	}

	var messages []MessageStoreSchema
	err := dao.db.NewSelect().
		Model(&messages).
		Where("session_id = ?", dao.sessionID).
		Where("uuid IN (?)", bun.In(messageUUIDs)).
		Scan(ctx)

	if err != nil {
		return nil, fmt.Errorf("unable to retrieve messages %w", err)
	}

	messageList := messagesFromStoreSchema(messages)

	return messageList, nil
}

// GetListBySession retrieves a list of messages for a session. The list is paginated.
func (dao *MessageDAO) GetListBySession(
	ctx context.Context,
	currentPage int,
	pageSize int) (*models.MessageListResponse, error) {

	var wg sync.WaitGroup
	var countErr error
	var count int

	wg.Add(1)
	go func() {
		defer wg.Done()
		// Get count of all messages for this session
		count, countErr = dao.db.NewSelect().
			Model(&MessageStoreSchema{}).
			Where("session_id = ?", dao.sessionID).
			Count(ctx)
	}()

	var messages []MessageStoreSchema
	err := dao.db.NewSelect().
		Model(&messages).
		Where("session_id = ?", dao.sessionID).
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
	for i, msg := range messages {
		messageList[i] = models.Message{
			UUID:       msg.UUID,
			CreatedAt:  msg.CreatedAt,
			Role:       msg.Role,
			Content:    msg.Content,
			TokenCount: msg.TokenCount,
			Metadata:   msg.Metadata,
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
func (dao *MessageDAO) Update(ctx context.Context,
	message *models.Message,
	includeContent bool,
	isPrivileged bool) error {
	if message.UUID == uuid.Nil {
		return fmt.Errorf("message UUID cannot be nil")
	}
	tx, err := dao.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	// Don't update the Metadata field here. We do this via a merge below.
	messageDB := MessageStoreSchema{
		Role:       message.Role,
		Content:    message.Content,
		TokenCount: message.TokenCount,
	}

	columns := []string{"token_count"}
	if includeContent {
		columns = append(columns, "role", "content")
	}

	r, err := tx.NewUpdate().
		Model(&messageDB).
		Column(columns...).
		Where("session_id = ?", dao.sessionID).
		Where("uuid = ?", message.UUID).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to update message: %w", err)
	}

	rows, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get affected rows: %w", err)
	}
	if rows == 0 {
		return models.NewNotFoundError(fmt.Sprintf("message %s not found", message.UUID))
	}

	// Update metadata
	if message.Metadata != nil {
		err = dao.updateMetadata(ctx, tx, message.UUID, message.Metadata, isPrivileged)
		if err != nil {
			return fmt.Errorf("failed to update message metadata: %w", err)
		}
	}

	err = tx.Commit()
	if err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}

	return nil
}

// UpdateMany updates a batch of messages by their UUIDs. Metadata is updated via a merge.
func (dao *MessageDAO) UpdateMany(ctx context.Context,
	messages []models.Message,
	includeContent bool,
	isPrivileged bool) error {
	tx, err := dao.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	messagesDB := make([]MessageStoreSchema, len(messages))
	for i, msg := range messages {
		if msg.UUID == uuid.Nil {
			return fmt.Errorf("message UUID cannot be nil")
		}
		messagesDB[i] = MessageStoreSchema{
			UUID:       msg.UUID,
			Role:       msg.Role,
			Content:    msg.Content,
			TokenCount: msg.TokenCount,
		}
	}

	updatedValues := dao.db.NewValues(&messagesDB)

	query := dao.db.NewUpdate().
		With("_data", updatedValues).
		Model(&messagesDB).
		TableExpr("_data").
		Set("token_count = _data.token_count")

	if includeContent {
		query = query.Set("role = _data.role").
			Set("content = _data.content")
	}

	_, err = query.
		Where("m.uuid = _data.uuid").
		Where("m.session_id = ?", dao.sessionID).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to update messages: %w", err)
	}

	// Update metadata
	for _, msg := range messages {
		if msg.Metadata != nil {
			err = dao.updateMetadata(ctx, tx, msg.UUID, msg.Metadata, isPrivileged)
			if err != nil {
				return fmt.Errorf("failed to update message metadata: %w", err)
			}
		}
	}

	err = tx.Commit()
	if err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}

	return nil
}

// updateMetadata updates the metadata for a message by its UUID. Metadata is updated via a merge.
// An advisory lock is acquired on the message UUID to prevent concurrent updates to the metadata.
func (dao *MessageDAO) updateMetadata(
	ctx context.Context,
	tx bun.IDB, // use bun.IDB interface to make it easier to test
	messageUUID uuid.UUID,
	metadata map[string]interface{},
	isPrivileged bool,
) error {
	// Acquire a lock for this Message UUID. This is to prevent concurrent updates
	// to the message metadata.
	lockID, err := acquireAdvisoryLock(ctx, tx, messageUUID.String())
	if err != nil {
		return fmt.Errorf("failed to acquire advisory lock: %w", err)
	}
	defer func(ctx context.Context, db bun.IDB, lockID uint64) {
		err := releaseAdvisoryLock(ctx, db, lockID)
		if err != nil {
			log.Errorf("failed to release advisory lock: %v", err)
		}
	}(ctx, tx, lockID)

	mergedMetadata, err := mergeMetadata(
		ctx,
		tx,
		"uuid",
		messageUUID.String(),
		"message",
		metadata,
		isPrivileged,
	)
	if err != nil {
		return fmt.Errorf("failed to merge message metadata: %w", err)
	}

	_, err = tx.NewUpdate().
		Model(&MessageStoreSchema{}).
		Column("metadata").
		Where("session_id = ?", dao.sessionID).
		Where("uuid = ?", messageUUID).
		Set("metadata = ?", mergedMetadata).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to update message metadata: %w", err)
	}

	return nil
}

func (dao *MessageDAO) Delete(ctx context.Context, messageUUID uuid.UUID) error {
	if messageUUID == uuid.Nil {
		return fmt.Errorf("message UUID cannot be nil")
	}

	tx, err := dao.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	// Delete embeddings, if any
	_, err = tx.NewDelete().
		Model(&MessageVectorStoreSchema{}).
		Where("session_id = ?", dao.sessionID).
		Where("message_uuid = ?", messageUUID).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to delete message embeddings: %w", err)
	}

	// Delete the message
	r, err := tx.NewDelete().
		Model(&MessageStoreSchema{}).
		Where("session_id = ?", dao.sessionID).
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
		return models.NewNotFoundError(fmt.Sprintf("message %s not found", messageUUID))
	}

	err = tx.Commit()
	if err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}
	return nil
}

// CreateEmbeddings saves message embeddings for a set of given messages
func (dao *MessageDAO) CreateEmbeddings(
	ctx context.Context,
	embeddings []models.TextData,
) error {
	if len(embeddings) == 0 {
		return errors.New("no embeddings received")
	}

	embeddingVectors := make([]MessageVectorStoreSchema, len(embeddings))
	for i, e := range embeddings {
		embeddingVectors[i] = MessageVectorStoreSchema{
			SessionID:   dao.sessionID,
			Embedding:   pgvector.NewVector(e.Embedding),
			MessageUUID: e.TextUUID,
			IsEmbedded:  true,
		}
	}

	_, err := dao.db.NewInsert().
		Model(&embeddingVectors).
		Exec(ctx)

	if err != nil {
		return fmt.Errorf("failed to insert message vectors %w", err)
	}

	return nil
}

func (dao *MessageDAO) GetEmbedding(ctx context.Context, messageUUID uuid.UUID) (*models.TextData, error) {
	var result struct {
		MessageStoreSchema
		MessageVectorStoreSchema
	}
	_, err := dao.db.NewSelect().
		Table("message_embedding").
		Join("JOIN message").
		JoinOn("message_embedding.message_uuid = message.uuid").
		ColumnExpr("message.content").
		ColumnExpr("message_embedding.*").
		Where("message_embedding.session_id = ?", dao.sessionID).
		Where("message_embedding.message_uuid = ?", messageUUID).
		Where("message.deleted_at IS NULL").
		Exec(ctx, &result)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, models.NewNotFoundError(fmt.Sprintf("embedding for message %s not found", messageUUID))
		}
		return nil, fmt.Errorf("failed to get message vectors %w", err)
	}

	return &models.TextData{
		Embedding: result.Embedding.Slice(),
		TextUUID:  result.MessageUUID,
		Text:      result.Content,
	}, nil
}

// GetEmbeddingListBySession retrieves all message embeddings for a session.
func (dao *MessageDAO) GetEmbeddingListBySession(ctx context.Context) ([]models.TextData, error) {
	var results []struct {
		MessageStoreSchema
		MessageVectorStoreSchema
	}
	_, err := dao.db.NewSelect().
		Table("message_embedding").
		Join("JOIN message").
		JoinOn("message_embedding.message_uuid = message.uuid").
		ColumnExpr("message.content").
		ColumnExpr("message_embedding.*").
		Where("message_embedding.session_id = ?", dao.sessionID).
		Where("message.deleted_at IS NULL").
		Exec(ctx, &results)
	if err != nil {
		return nil, fmt.Errorf("failed to get message vectors %w", err)
	}

	embeddings := make([]models.TextData, len(results))
	for i, vectorStoreRecord := range results {
		embeddings[i] = models.TextData{
			Embedding: vectorStoreRecord.Embedding.Slice(),
			TextUUID:  vectorStoreRecord.MessageUUID,
			Text:      vectorStoreRecord.Content,
		}
	}

	return embeddings, nil
}

// getMessageIndex retrieves the index of the last summary point for a session
// This is a bit of a hack since UUIDs are not sortable.
// If the SummaryPoint does not exist (for e.g. if it was deleted), returns 0.
func getMessageIndex(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	summaryPointUUID uuid.UUID,
) (int64, error) {
	var message MessageStoreSchema

	err := db.NewSelect().
		Model(&message).
		Column("id").
		Where("session_id = ? AND uuid = ?", sessionID, summaryPointUUID).
		Scan(ctx)

	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			log.Warningf(
				"unable to retrieve last summary point for %s: %s",
				summaryPointUUID,
				err,
			)
		} else {
			return 0, store.NewStorageError("unable to retrieve last summary point for %s", err)
		}

		return 0, nil
	}

	return message.ID, nil
}

// MessagesFromStoreSchema converts a slice of MessageStoreSchema into a slice of models.Message.
func messagesFromStoreSchema(messages []MessageStoreSchema) []models.Message {
	messageList := make([]models.Message, len(messages))
	for i, msg := range messages {
		messageList[i] = models.Message{
			UUID:       msg.UUID,
			CreatedAt:  msg.CreatedAt,
			Role:       msg.Role,
			Content:    msg.Content,
			TokenCount: msg.TokenCount,
			Metadata:   msg.Metadata,
		}
	}
	return messageList
}
