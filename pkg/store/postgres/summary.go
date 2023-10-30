package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/pgvector/pgvector-go"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/store"
	"github.com/google/uuid"
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

func updateSummaryMetadata(
	ctx context.Context,
	db *bun.DB,
	summary *models.Summary,
) (*models.Summary, error) {
	if summary.UUID == uuid.Nil {
		return nil, errors.New("summary UUID cannot be empty")
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to start transaction: %w", err)
	}
	defer rollbackOnError(tx)

	metadata, err := mergeMetadata(
		ctx,
		tx,
		"uuid",
		summary.UUID.String(),
		"summary",
		summary.Metadata,
		true,
	)
	if err != nil {
		return nil, fmt.Errorf("failed to update summary metadata: %w", err)
	}

	pgSummary := &SummaryStoreSchema{
		UUID:     summary.UUID,
		Metadata: metadata,
	}

	_, err = tx.NewUpdate().
		Model(pgSummary).
		Column("metadata").
		Where("uuid = ?", summary.UUID).
		Exec(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to update summary metadata: %w", err)
	}

	err = tx.Commit()
	if err != nil {
		return nil, fmt.Errorf("failed to commit transaction: %w", err)
	}

	return summary, nil
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

func getSummaryByUUID(ctx context.Context,
	_ *models.AppState,
	db *bun.DB,
	sessionID string,
	uuid uuid.UUID) (*models.Summary, error) {
	if sessionID == "" {
		return nil, store.NewStorageError("sessionID cannot be empty", nil)
	}

	summary := SummaryStoreSchema{}
	err := db.NewSelect().
		Model(&summary).
		Where("session_id = ?", sessionID).
		Where("uuid = ?", uuid).
		Scan(ctx)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, models.NewNotFoundError("summary " + uuid.String())
		}
		return &models.Summary{}, store.NewStorageError("failed to get session", err)
	}

	return &models.Summary{
		UUID:             summary.UUID,
		CreatedAt:        summary.CreatedAt,
		Content:          summary.Content,
		SummaryPointUUID: summary.SummaryPointUUID,
		Metadata:         summary.Metadata,
		TokenCount:       summary.TokenCount,
	}, nil
}

func putSummaryEmbedding(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	embedding *models.TextData,
) error {
	if sessionID == "" {
		return store.NewStorageError("sessionID cannot be empty", nil)
	}

	record := SummaryVectorStoreSchema{
		SessionID:   sessionID,
		Embedding:   pgvector.NewVector(embedding.Embedding),
		SummaryUUID: embedding.TextUUID,
		IsEmbedded:  true,
	}
	_, err := db.NewInsert().Model(&record).Exec(ctx)
	if err != nil {
		return store.NewStorageError("failed to insert summary embedding", err)
	}

	return nil
}

// Retrieves all summary embeddings for a session. Note: Does not return the summary content.
func getSummaryEmbeddings(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
) ([]models.TextData, error) {
	if sessionID == "" {
		return nil, errors.New("sessionID cannot be empty")
	}

	embeddings := make([]SummaryVectorStoreSchema, 0)
	err := db.NewSelect().
		Model(&embeddings).
		Where("session_id = ?", sessionID).
		Where("is_embedded = ?", true).
		Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get summary embeddings %w", err)
	}

	retEmbeddings := make([]models.TextData, len(embeddings))
	for i, embedding := range embeddings {
		retEmbeddings[i] = models.TextData{
			TextUUID:  embedding.SummaryUUID,
			Embedding: embedding.Embedding.Slice(),
		}
	}

	return retEmbeddings, nil
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
