package postgres

import (
	"context"
	"fmt"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/memorystore"

	"github.com/getzep/zep/pkg/models"
	"github.com/jinzhu/copier"
	"github.com/uptrace/bun"
)

func putDocuments(
	ctx context.Context,
	db *bun.DB,
	collectionName string,
	documents []*models.Document,
) error {
	if len(documents) == 0 {
		return nil
	}

	collection, err := getCollection(ctx, db, collectionName)
	if err != nil {
		return memorystore.NewStorageError("failed to get collection: %w", err)
	}

	documentRows := make([]*DocumentSchemaTemplate, len(documents))
	for i, document := range documents {
		documentRow := &DocumentSchemaTemplate{}
		err := copier.Copy(documentRow, document)
		if err != nil {
			return fmt.Errorf("failed to copy document: %w", err)
		}
		documentRows[i] = documentRow
	}

	_, err = db.NewInsert().
		Model(&documentRows).
		ModelTableExpr(collection.TableName).
		Column("content", "metadata").
		On("CONFLICT (uuid) DO UPDATE").
		Returning("uuid").
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to insert documents: %w", err)
	}

	// update the UUIDs of the documents
	for i, documentRow := range documentRows {
		documents[i].UUID = documentRow.UUID
	}

	return nil
}

// getDocuments retrieves all documents from a collection in the DB. If limit is greater than 0, it will
// only retrieve that many documents.
func getDocuments(
	ctx context.Context,
	db *bun.DB,
	collectionName string,
	limit int,
) ([]*models.Document, error) {
	collection, err := getCollection(ctx, db, collectionName)
	if err != nil {
		return nil, memorystore.NewStorageError("failed to get collection: %w", err)
	}

	// we have to run a raw query as the embeddings aren't in the DocumentSchemaTemplate
	// this also allows us to scan directly into the Document struct
	sliceLen := 0
	limitString := ""
	if limit > 0 {
		sliceLen = limit
		limitString = fmt.Sprintf(" LIMIT %d", limit)
	}
	documents := make([]*models.Document, sliceLen)
	query := "SELECT uuid, created_at, content, metadata, embedding FROM ? WHERE deleted_at IS NULL" + limitString
	err = db.NewRaw(query, bun.Ident(collection.TableName)).Scan(ctx, &documents)
	if err != nil {
		return nil, fmt.Errorf("failed to get documents: %w", err)
	}

	return documents, nil
}

func getDocument(
	ctx context.Context,
	db *bun.DB,
	collectionName string,
	documentUUID uuid.UUID,
) (*models.Document, error) {
	collection, err := getCollection(ctx, db, collectionName)
	if err != nil {
		return nil, memorystore.NewStorageError("failed to get collection: %w", err)
	}

	// we have to run a raw query as the embeddings aren't in the DocumentSchemaTemplate
	// this also allows us to scan directly into the Document struct
	documents := make([]*models.Document, 1)
	query := "SELECT uuid, created_at, content, metadata, embedding FROM ? WHERE uuid = ? AND deleted_at IS NULL"
	err = db.NewRaw(query, bun.Ident(collection.TableName), documentUUID).Scan(ctx, &documents)
	if err != nil {
		return nil, fmt.Errorf("failed to get documents: %w", err)
	}

	if len(documents) == 0 {
		return nil, fmt.Errorf("document not found: %s", documentUUID.String())
	}

	return documents[0], nil
}
