package memorystore

import (
	"context"
	"errors"
	"math"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

func searchMessages(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	sessionID string,
	query *models.SearchPayload,
	limit int,
) ([]models.SearchResult, error) {
	if query == nil {
		return nil, NewStorageError("nil query received", nil)
	}

	s := query.Text
	if s == "" {
		return nil, NewStorageError("empty query", errors.New("empty query"))
	}

	if appState == nil {
		return nil, NewStorageError("nil appState received", nil)
	}

	log.Debugf("searchMessages called for session %s", sessionID)

	if limit == 0 {
		limit = 10
	}

	e, err := llms.EmbedMessages(ctx, appState, []string{s})
	if err != nil {
		return nil, NewStorageError("failed to embed query", err)
	}
	vector := pgvector.NewVector(e[0].Embedding)

	var results []models.SearchResult
	err = db.NewSelect().
		TableExpr("message_embedding AS me").
		Join("JOIN message AS m").
		JoinOn("me.message_uuid = m.uuid").
		ColumnExpr("m.uuid AS message__uuid").
		ColumnExpr("m.created_at AS message__created_at").
		ColumnExpr("m.role AS message__role").
		ColumnExpr("m.content AS message__content").
		ColumnExpr("m.metadata AS message__metadata").
		ColumnExpr("m.token_count AS message__token_count").
		ColumnExpr("1 - (embedding <=> ? ) AS dist", vector).
		Where("m.session_id = ?", sessionID).
		Order("dist DESC").
		Limit(limit).
		Scan(ctx, &results)
	if err != nil {
		return nil, NewStorageError("memory searchMessages failed", err)
	}

	// some results may be returned where distance is NaN. This is a race between
	// newly added messages and the search query. We filter these out.
	var filteredResults []models.SearchResult
	for _, result := range results {
		if !math.IsNaN(result.Dist) {
			filteredResults = append(filteredResults, result)
		}
	}
	log.Debugf("searchMessages completed for session %s", sessionID)

	return filteredResults, nil
}
