package postgres

import (
	"context"
	"database/sql"
	"errors"

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
		if errors.Is(err, sql.ErrNoRows) {
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

// GetSummaryList returns a list of summaries for a session
func getSummaryList(ctx context.Context,
	db *bun.DB,
	sessionID string,
	currentPage int,
	pageSize int,
) (*models.SummaryListResponse, error) {
	if sessionID == "" {
		return nil, store.NewStorageError("sessionID cannot be empty", nil)
	}

	summariesDB := make([]SummaryStoreSchema, 0)
	err := db.NewSelect().
		Model(&summariesDB).
		Where("session_id = ?", sessionID).
		Where("deleted_at IS NULL").
		Order("created_at ASC").
		Offset((currentPage - 1) * pageSize).
		Limit(pageSize).
		Scan(ctx)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, store.NewStorageError("failed to get sessions", err)
	}

	summaries := make([]models.Summary, len(summariesDB))
	for i, summary := range summariesDB {
		summaries[i] = models.Summary{
			UUID:             summary.UUID,
			CreatedAt:        summary.CreatedAt,
			Content:          summary.Content,
			SummaryPointUUID: summary.SummaryPointUUID,
			Metadata:         summary.Metadata,
			TokenCount:       summary.TokenCount,
		}
	}

	respSummary := models.SummaryListResponse{
		Summaries: summaries,
		RowCount:  len(summaries),
	}

	return &respSummary, nil
}
