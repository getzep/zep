package postgres

import (
	"context"
	"database/sql"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/store"
	"github.com/jinzhu/copier"
	"github.com/uptrace/bun"
)

// putSummary stores a new summary for a session. The recentMessageID is the UUID of the most recent
// message in the session when the summary was created.
func putSummary(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	summary *models.Summary,
) (*models.Summary, error) {
	if sessionID == "" {
		return nil, store.NewStorageError("sessionID cannot be empty", nil)
	}

	pgSummary := SummaryStoreSchema{}
	err := copier.Copy(&pgSummary, summary)
	if err != nil {
		return nil, store.NewStorageError("failed to copy summary", err)
	}

	pgSummary.SessionID = sessionID

	_, err = db.NewInsert().Model(&pgSummary).Exec(ctx)
	if err != nil {
		return nil, store.NewStorageError("failed to Create summary", err)
	}

	retSummary := models.Summary{}
	err = copier.Copy(&retSummary, &pgSummary)
	if err != nil {
		return nil, store.NewStorageError("failed to copy summary", err)
	}

	return &retSummary, nil
}

// getSummary returns the most recent summary for a session
func getSummary(ctx context.Context, db *bun.DB, sessionID string) (*models.Summary, error) {
	summary := SummaryStoreSchema{}
	err := db.NewSelect().
		Model(&summary).
		Where("session_id = ?", sessionID).
		Where("deleted_at IS NULL").
		// Get the most recent summary
		Order("created_at DESC").
		Limit(1).
		Scan(ctx)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return &models.Summary{}, store.NewStorageError("failed to get session", err)
	}

	respSummary := models.Summary{}
	err = copier.Copy(&respSummary, &summary)
	if err != nil {
		return nil, store.NewStorageError("failed to copy summary", err)
	}
	return &respSummary, nil
}
