package memorystore

import (
	"context"

	"github.com/getzep/zep/pkg/models"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

func getMessageVectors(ctx context.Context,
	db *bun.DB,
	sessionID string) ([]models.Embeddings, error) {
	var results []struct {
		PgMessageStore
		PgMessageVectorStore
	}
	// TODO: Check that excluding deleted
	_, err := db.NewSelect().
		Table("message_embedding").
		Join("JOIN message").
		JoinOn("message_embedding.message_uuid = message.uuid").
		ColumnExpr("message.content").
		ColumnExpr("message_embedding.*").
		Where("message_embedding.is_embedded = ?", true).
		Where("message_embedding.session_id = ?", sessionID).
		Exec(ctx, &results)
	if err != nil {
		return nil, NewStorageError("failed to get message vectors", err)
	}

	embeddings := make([]models.Embeddings, len(results))
	for i, vectorStoreRecord := range results {
		embeddings[i] = models.Embeddings{
			Embedding: vectorStoreRecord.Embedding.Slice(),
			TextUUID:  vectorStoreRecord.MessageUUID,
			Text:      vectorStoreRecord.Content,
		}
	}

	return embeddings, nil
}

func putEmbeddings(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	embeddings []models.Embeddings,
) error {
	if embeddings == nil {
		return NewStorageError("nil embeddings received", nil)
	}
	if len(embeddings) == 0 {
		return NewStorageError("no embeddings received", nil)
	}

	embeddingVectors := make([]PgMessageVectorStore, len(embeddings))
	for i, e := range embeddings {
		embeddingVectors[i] = PgMessageVectorStore{
			SessionID:   sessionID,
			Embedding:   pgvector.NewVector(e.Embedding),
			MessageUUID: e.TextUUID,
			IsEmbedded:  true,
		}
	}

	_, err := db.NewInsert().
		Model(&embeddingVectors).
		Exec(ctx)

	if err != nil {
		return NewStorageError("failed to insert message vectors", err)
	}

	return nil
}
