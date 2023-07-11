package postgres

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/getzep/zep/pkg/memorystore"

	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

type DocumentCollection struct {
	UUID                uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt           time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp"`
	UpdatedAt           time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	Name                string                 `bun:",notnull,unique"`
	Description         string                 `bun:",notnull"`
	Metadata            map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"`
	TableName           string                 `bun:",notnull"`
	EmbeddingModelName  string                 `bun:",notnull"`
	EmbeddingDimensions int                    `bun:",notnull"`
	DistanceFunction    string                 `bun:",notnull"`
	IsNormalized        bool                   `bun:",notnull"`
	IsIndexed           bool                   `bun:",notnull"`
}

type DocumentBase struct {
	UUID      uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp"`
	UpdatedAt time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt time.Time              `bun:"type:timestamptz,soft_delete,nullzero"`
	Content   string                 `bun:",notnull"`
	Metadata  map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"`
}

type Document struct {
	DocumentBase
	// we use real[] here to get around having to explicitly use vector and define the vector width
	Embedding []float32 `bun:"type:real[]"`
}

// put inserts a collection into the collections table and creates a
// table for the collection's documents. If the collection already exists in the collection table,
// it will be updated.
func (c *DocumentCollection) put(
	ctx context.Context,
	db *bun.DB,
) error {
	if c.TableName == "" {
		tableName, err := generateDocumentTableName(c)
		if err != nil {
			return fmt.Errorf("failed to generate collection table name: %w", err)
		}
		c.TableName = tableName
	}

	_, err := db.NewInsert().
		Model(c).
		ModelTableExpr("document_collection").
		On("CONFLICT (uuid) DO UPDATE").
		Returning("*").
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to insert collection: %w", err)
	}

	// Create the document table for the collection. It will only be created if
	// it doesn't already exist.
	err = createDocumentTable(ctx, db, c.TableName, c.EmbeddingDimensions)
	if err != nil {
		return fmt.Errorf("failed to create document table: %w", err)
	}

	return nil
}

// TODO: handle 404
// getByName returns a collection from the collections table by name.
func (c *DocumentCollection) getByName(
	ctx context.Context,
	db *bun.DB,
) error {
	if c.Name == "" {
		return errors.New("collection name is required")
	}
	err := db.NewSelect().
		Model(c).
		ModelTableExpr("document_collection").
		Where("name = ?", c.Name).
		Scan(ctx)
	if err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	return nil
}

// TODO: handle 404
// getAll returns a list of all collections from the collections table.
func (c *DocumentCollection) getAll(
	ctx context.Context,
	db *bun.DB,
) ([]DocumentCollection, error) {
	var collections []DocumentCollection
	err := db.NewSelect().Model(&collections).ModelTableExpr("document_collection").Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get collection list: %w", err)
	}

	return collections, nil
}

// TODO: handle 404
// delete deletes a collection from the collections table and drops the
// collection's document table.
func (c *DocumentCollection) delete(ctx context.Context, db *bun.DB) error {
	if c.Name == "" {
		return errors.New("collection name is required")
	}
	// start a transaction
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	err = c.getByName(ctx, db)
	if err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	// Delete the collection row.
	err = deleteCollectionRow(ctx, tx, c.Name)
	if err != nil {
		return err
	}

	// Drop the document table for the collection.
	err = dropDocumentTable(ctx, tx, c.TableName)
	if err != nil {
		return fmt.Errorf("failed to drop document table: %w", err)
	}

	err = tx.Commit()
	if err != nil {
		return fmt.Errorf("failed to commit transaction: %w", err)
	}

	return nil
}

// putDocuments upserts the given documents into the given collection. Only the content and metadata fields are
// updated. The document's UUID is used to determine whether to insert or update the document.
// NOTE: Does not persist Document Embeddings.
func (c *DocumentCollection) putDocuments(
	ctx context.Context,
	db *bun.DB,
	documents []*Document,
) error {
	if len(documents) == 0 {
		return nil
	}
	if c.Name == "" {
		return errors.New("collection name cannot be empty")
	}
	err := c.getByName(ctx, db)
	if err != nil {
		return memorystore.NewStorageError("failed to get collection: %w", err)
	}
	_, err = db.NewInsert().
		Model(&documents).
		ModelTableExpr(c.TableName).
		Column("content", "metadata").
		On("CONFLICT (uuid) DO UPDATE").
		Returning("*").
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to insert documents: %w", err)
	}

	return nil
}

// getDocuments retrieves documents. If `documents` is non-Nil, it will use the document UUIDs to retrieve
// these documents. Otherwise, it will retrieve all documents. If limit is greater than 0, it will
// only retrieve limit many documents.
// TODO: 404
func (c *DocumentCollection) getDocuments(
	ctx context.Context,
	db *bun.DB,
	limit int,
	documents []*Document,
) ([]*Document, error) {
	if c.Name == "" {
		return nil, errors.New("collection name cannot be empty")
	}
	err := c.getByName(ctx, db)
	if err != nil {
		return nil, fmt.Errorf("failed to get collection: %w", err)
	}

	query := db.NewSelect().
		Model(&documents).
		ModelTableExpr(c.TableName+" AS document").
		Column("uuid", "created_at", "content", "metadata").
		// cast the vectors to a float array
		ColumnExpr("embedding::real[]")

	if len(documents) > 0 {
		uuids := make([]uuid.UUID, len(documents))
		for i, doc := range documents {
			uuids[i] = doc.UUID
		}

		query = query.Where("uuid IN (?)", bun.In(uuids))
	}

	if limit > 0 {
		query = query.Limit(limit)
	}

	err = query.
		Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get documents: %w", err)
	}

	return documents, nil
}

// deleteDocument deletes a single document from a collection in the DB, identified by its UUID.
func (c *DocumentCollection) deleteDocumentByUUID(
	ctx context.Context,
	db *bun.DB,
	documentUUID uuid.UUID,
) error {
	if c.Name == "" {
		return errors.New("collection name cannot be empty")
	}
	err := c.getByName(ctx, db)
	if err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	r, err := db.NewDelete().
		Model(&Document{}).
		ModelTableExpr(c.TableName).
		Where("uuid = ?", documentUUID).
		// ModelTableExpr isn't being set on the auto-soft delete in the WHERE clause
		// so we have to use WhereAllWithDeleted to avoid adding deleted_at Is NOT NULL
		WhereAllWithDeleted().
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to delete document: %w", err)
	}

	rowsEffected, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows effected: %w", err)
	}
	if rowsEffected == 0 {
		return fmt.Errorf("document not found: %s", documentUUID.String())
	}

	return nil
}

// putDocumentEmbeddings updates the embeddings of a set of documents. The documentEmbeddings
// argument must include the UUIDs and embeddings of the documents to be updated. Other fields are ignored.
// If the UUIDs do not exist in the collection, an error is returned.
func (c *DocumentCollection) putDocumentEmbeddings(
	ctx context.Context,
	db *bun.DB,
	documentEmbeddings []*Document,
) error {
	if len(documentEmbeddings) == 0 {
		return nil
	}

	err := c.getByName(ctx, db)
	if err != nil {
		return memorystore.NewStorageError("failed to get collection: %w", err)
	}

	values := db.NewValues(&documentEmbeddings).Column("uuid", "embedding")
	r, err := db.NewUpdate().
		With("_data", values).
		ModelTableExpr(c.TableName + " AS document").
		TableExpr("_data").
		Set("embedding = _data.embedding").
		Where("document.uuid = _data.uuid").
		Returning(""). // we don't need to return anything
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to update document embeddings: %w", err)
	}

	rowsEffected, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows effected: %w", err)
	}
	if rowsEffected != int64(len(documentEmbeddings)) {
		return fmt.Errorf(
			"failed to update all document embeddings: %d != %d",
			rowsEffected,
			len(documentEmbeddings),
		)
	}

	return nil
}

func deleteCollectionRow(
	ctx context.Context,
	tx bun.Tx,
	collectionName string,
) error {
	r, err := tx.NewDelete().Table("document_collection").Where(
		"name = ?", collectionName,
	).Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to delete collection: %w", err)
	}
	rows, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected: %w", err)
	}
	if rows == 0 {
		return fmt.Errorf("collection not found: %s", collectionName)
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
func generateDocumentTableName(collection *DocumentCollection) (string, error) {
	if collection == nil {
		return "", errors.New("collection is nil")
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
