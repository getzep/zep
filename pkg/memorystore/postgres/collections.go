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
		tableName, err := generateDocumentTableName(collection)
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

// TODO: handle 404
// getCollection returns a collection from the collections table by name.
func getCollection(
	ctx context.Context,
	db *bun.DB,
	collectionName string,
) (*models.DocumentCollection, error) {
	collectionRow := &DocumentCollectionSchema{}
	err := db.NewSelect().Model(collectionRow).Where("name = ?", collectionName).Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get collection: %w", err)
	}

	collection := &models.DocumentCollection{}
	err = copier.Copy(collection, collectionRow)
	if err != nil {
		return nil, fmt.Errorf("failed to copy collection: %w", err)
	}

	return collection, nil
}

// TODO: handle 404
// getCollectionList returns a list of all collections from the collections table.
func getCollectionList(
	ctx context.Context,
	db *bun.DB,
) ([]models.DocumentCollection, error) {
	var collectionRows []DocumentCollectionSchema
	err := db.NewSelect().Model(&collectionRows).Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get collection list: %w", err)
	}

	collections := make([]models.DocumentCollection, len(collectionRows))
	for i := range collectionRows {
		collection := &models.DocumentCollection{}
		err = copier.Copy(collection, collectionRows[i])
		if err != nil {
			return nil, fmt.Errorf("failed to copy collection: %w", err)
		}
		collections[i] = *collection
	}

	return collections, nil
}

// TODO: handle 404
// deleteCollection deletes a collection from the collections table and drops the
// collection's document table.
func deleteCollection(ctx context.Context, db *bun.DB, collectionName string) error {
	// start a transaction
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	collection, err := getCollection(ctx, db, collectionName)
	if err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	// Delete the collection row.
	err = deleteCollectionRow(ctx, tx, collection)
	if err != nil {
		return err
	}

	// Drop the document table for the collection.
	err = dropDocumentTable(ctx, tx, collection.TableName)
	if err != nil {
		return fmt.Errorf("failed to drop document table: %w", err)
	}

	err = tx.Commit()
	if err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}

	return nil
}

func deleteCollectionRow(
	ctx context.Context,
	tx bun.Tx,
	collection *models.DocumentCollection,
) error {
	r, err := tx.NewDelete().Table("document_collection").Where(
		"name = ?", collection.Name,
	).Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to delete collection: %w", err)
	}
	rows, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected: %w", err)
	}
	if rows == 0 {
		return fmt.Errorf("collection not found: %s", collection.Name)
	}
	return nil
}

// dropDocumentTable drops a document table. Used when a collection is deleted.
func dropDocumentTable(ctx context.Context, db bun.IDB, tableName string) error {
	_, err := db.NewDropTable().Table(tableName).IfExists().Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to drop document table: %w", err)
	}

	return nil
}

// generateDocumentTableName generates a table name for a given collection.
// The tableName needs to be less than 63 characters long, so we limit the
// collection name to 47 characters
func generateDocumentTableName(collection *models.DocumentCollection) (string, error) {
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
