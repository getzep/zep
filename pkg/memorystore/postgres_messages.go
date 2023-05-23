package memorystore

import (
	"context"
	"database/sql"

	"github.com/getzep/zep/internal"
	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
	"github.com/jinzhu/copier"
	"github.com/uptrace/bun"
)

// putMessages stores a new or updates existing messages for a session. Existing
// messages are determined by message UUID. Sessions are created if they do not
// exist.
func putMessages(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	messages []models.Message,
) ([]models.Message, error) {
	if len(messages) == 0 {
		log.Warn("putMessages called with no messages")
		return nil, nil
	}
	log.Debugf("putMessages called for session %s with %d messages", sessionID, len(messages))

	// Create or update a Session
	_, err := putSession(ctx, db, sessionID, nil)
	if err != nil {
		return nil, NewStorageError("failed to put session", err)
	}

	pgMessages := make([]PgMessageStore, len(messages))
	err = copier.Copy(&pgMessages, &messages)
	if err != nil {
		return nil, NewStorageError("failed to copy messages to pgMessages", err)
	}

	for i := range pgMessages {
		pgMessages[i].SessionID = sessionID
	}

	// TODO: Remove metadata and insert/update separately
	_, err = db.NewInsert().
		Model(&pgMessages).
		Column("id", "created_at", "uuid", "session_id", "role", "content", "token_count", "metadata").
		On("CONFLICT (uuid) DO UPDATE").
		Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to save memories to store", err)
	}

	retMessages := make([]models.Message, len(messages))
	err = copier.Copy(&retMessages, &pgMessages)
	if err != nil {
		return nil, NewStorageError("failed to copy pgMessages to retMessages", err)
	}

	log.Debugf("putMessages completed for session %s with %d messages", sessionID, len(messages))

	return retMessages, nil
}

// getMessages retrieves messages from the memory store. If lastNMessages is 0, the last SummaryPoint is retrieved.
func getMessages(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	memoryWindow int,
	summary *models.Summary,
	lastNMessages int,
) ([]models.Message, error) {
	if sessionID == "" {
		return nil, NewStorageError("sessionID cannot be empty", nil)
	}
	if memoryWindow == 0 {
		return nil, NewStorageError("memory.message_window must be greater than 0", nil)
	}

	var messages []PgMessageStore
	var err error
	if lastNMessages > 0 {
		messages, err = fetchLastNMessages(ctx, db, sessionID, lastNMessages)
	} else {
		messages, err = fetchMessagesAfterSummaryPoint(ctx, db, sessionID, summary)
	}
	if err != nil {
		return nil, NewStorageError("failed to get messages", err)
	}
	if len(messages) == 0 {
		return nil, nil
	}

	messageList := make([]models.Message, len(messages))
	err = copier.Copy(&messageList, &messages)
	if err != nil {
		return nil, NewStorageError("failed to copy messages", err)
	}

	return messageList, nil
}

// fetchMessagesAfterSummaryPoint retrieves messages after a summary point. If the summaryPointIndex
// is 0, all undeleted messages are retrieved.
func fetchMessagesAfterSummaryPoint(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	summary *models.Summary,
) ([]PgMessageStore, error) {
	var summaryPointIndex int64
	var err error
	if summary != nil {
		summaryPointIndex, err = getSummaryPointIndex(ctx, db, sessionID, summary.SummaryPointUUID)
		if err != nil {
			return nil, NewStorageError("unable to retrieve summary", nil)
		}
	}

	messages := make([]PgMessageStore, 0)
	query := db.NewSelect().
		Model(&messages).
		Where("session_id = ?", sessionID).
		Order("id ASC")

	if summaryPointIndex > 0 {
		query.Where("id > ?", summaryPointIndex)
	}

	return messages, query.Scan(ctx)
}

// fetchLastNMessages retrieves the last N messages for a session, ordered by ID DESC
// and then reverses the slice so that the messages are in ascending order
func fetchLastNMessages(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	lastNMessages int,
) ([]PgMessageStore, error) {
	messages := make([]PgMessageStore, 0)
	query := db.NewSelect().
		Model(&messages).
		Where("session_id = ?", sessionID).
		Order("id DESC").
		Limit(lastNMessages)

	err := query.Scan(ctx)

	if err == nil && len(messages) > 0 {
		internal.ReverseSlice(messages)
	}

	return messages, err
}

// getSummaryPointIndex retrieves the index of the last summary point for a session
// This is a bit of a hack since UUIDs are not sortable.
// If the SummaryPoint does not exist (for e.g. if it was deleted), returns 0.
func getSummaryPointIndex(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	summaryPointUUID uuid.UUID,
) (int64, error) {
	var message PgMessageStore

	err := db.NewSelect().
		Model(&message).
		Column("id").
		Where("session_id = ? AND uuid = ?", sessionID, summaryPointUUID).
		Where("deleted_at IS NULL").
		Scan(ctx)

	if err != nil {
		if err == sql.ErrNoRows {
			log.Warningf(
				"unable to retrieve last summary point for %s: %s",
				summaryPointUUID,
				err,
			)
		} else {
			return 0, NewStorageError("unable to retrieve last summary point for %s", err)
		}

		return 0, nil
	}

	return message.ID, nil
}
