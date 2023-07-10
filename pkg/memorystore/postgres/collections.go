package postgres

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/uptrace/bun"

	"github.com/jinzhu/copier"

	"github.com/getzep/zep/pkg/models"
)

// putCollection inserts a collection into the collections table and creates a
// table for the collection's documents. If the collection already exists in the collection table,
// it will be updated.
func putCollection(ctx context.Context, db *bun.DB, collection *models.DocumentCollection) error {
	if collection.TableName == "" {
		tableName, err := generateCollectionTableName(collection)
		if err != nil {
			return fmt.Errorf("failed to generate collection table name: %w", err)
		}
		collection.TableName = tableName
	}

	err := putCollectionRow(ctx, db, collection)
	if err != nil {
		return fmt.Errorf("failed to put collection row: %w", err)
	}

	// Create the document table for the collection. It will only be created if
	// it doesn't already exist.
	err = createDocumentTable(ctx, db, collection.TableName, collection.EmbeddingDimensions)
	if err != nil {
		return fmt.Errorf("failed to create document table: %w", err)
	}

	return nil
}

// putCollectionRow inserts a collection into the collections table. It returns
// the UUID of the collection row.
func putCollectionRow(
	ctx context.Context,
	db *bun.DB,
	collection *models.DocumentCollection,
) error {
	collectionRow := &DocumentCollectionSchema{}
	err := copier.Copy(collectionRow, collection)
	if err != nil {
		return fmt.Errorf("failed to copy collection: %w", err)
	}

	_, err = db.NewInsert().Model(collectionRow).On("CONFLICT (uuid) DO UPDATE").Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to insert collection: %w", err)
	}

	return nil
}

// generateCollectionTableName generates a table name for a given collection.
// The tableName needs to be less than 63 characters long, so we limit the
// collection name to 47 characters
func generateCollectionTableName(collection *models.DocumentCollection) (string, error) {
	if collection == nil {
		return "", errors.New("collection is nil")
	}
	if collection.UUID == uuid.Nil {
		return "", errors.New("collection.UUID is nil")
	}
	if collection.Name == "" {
		return "", errors.New("collection.Name is empty")
	}
	if len(collection.Name) > 47 {
		return "", fmt.Errorf(
			"collection name too long: %d > 47 char maximum",
			len(collection.Name),
		)
	}
	if collection.EmbeddingDimensions == 0 {
		return "", errors.New("collection.EmbeddingDimensions is 0")
	}
	nameSlug := strings.ToLower(strings.ReplaceAll(collection.Name, " ", "_"))
	tableName := fmt.Sprintf(
		"docstore_%s_%d",
		nameSlug,
		collection.EmbeddingDimensions,
	)
	if len(tableName) > 63 {
		return "", fmt.Errorf("table name too long: %d > 63 char maximum", len(tableName))
	}
	return tableName, nil
}
