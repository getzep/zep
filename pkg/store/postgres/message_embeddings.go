package postgres

import (
	"context"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/store"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

func getMessageEmbeddings(ctx context.Context,
	db *bun.DB,
	sessionID string) ([]models.MessageEmbedding, error) {
	var results []struct {
		MessageStoreSchema
		MessageVectorStoreSchema
	}
	_, err := db.NewSelect().
		Table("message_embedding").
		Join("JOIN message").
		JoinOn("message_embedding.message_uuid = message.uuid").
		ColumnExpr("message.content").
		ColumnExpr("message_embedding.*").
		Where("message_embedding.session_id = ?", sessionID).
		Where("message.deleted_at IS NULL").
		Exec(ctx, &results)
	if err != nil {
		return nil, store.NewStorageError("failed to get message vectors", err)
	}

	embeddings := make([]models.MessageEmbedding, len(results))
	for i, vectorStoreRecord := range results {
		embeddings[i] = models.MessageEmbedding{
			Embedding: vectorStoreRecord.Embedding.Slice(),
			TextUUID:  vectorStoreRecord.MessageUUID,
			Text:      vectorStoreRecord.Content,
		}
	}

	return embeddings, nil
}

func putMessageEmbeddings(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	embeddings []models.MessageEmbedding,
) error {
	if embeddings == nil {
		return store.NewStorageError("nil embeddings received", nil)
	}
	if len(embeddings) == 0 {
		return store.NewStorageError("no embeddings received", nil)
	}

	embeddingVectors := make([]MessageVectorStoreSchema, len(embeddings))
	for i, e := range embeddings {
		embeddingVectors[i] = MessageVectorStoreSchema{
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
		return store.NewStorageError("failed to insert message vectors", err)
	}

	return nil
}
