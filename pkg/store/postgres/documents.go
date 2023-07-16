package postgres

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

type DocumentBase struct {
	UUID       uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt  time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp"`
	UpdatedAt  time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt  time.Time              `bun:"type:timestamptz,soft_delete,nullzero"`
	DocumentID string                 `bun:",unique"`
	Content    string                 `bun:""`
	Metadata   map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"`
}

type Document struct {
	DocumentBase
	// we use real[] here to get around having to explicitly use vector and define the vector width
	Embedding []float32 `bun:"type:real[]"`
}

func (d *Document) Marker() {}

var _ models.DocumentInterface = &Document{}

type DocumentCollection struct {
	db                  *bun.DB                `bun:"-"`
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

var _ models.DocumentCollectionInterface = &DocumentCollection{}

// Create inserts a collection into the collections table and creates a
// table for the collection's documents.
func (c *DocumentCollection) Create(
	ctx context.Context,
) error {
	// TODO: validate collection struct fields using validator
	if c.Name == "" {
		return errors.New("collection name is required")
	}
	if c.TableName == "" {
		tableName, err := generateDocumentTableName(c)
		if err != nil {
			return fmt.Errorf("failed to generate collection table name: %w", err)
		}
		c.TableName = tableName
	}

	c.Name = strings.ToLower(c.Name)

	_, err := c.db.NewInsert().
		Model(c).
		ModelTableExpr("document_collection").
		Returning("*").
		Exec(ctx)
	if err != nil {
		if strings.Contains(err.Error(), "duplicate key value violates unique constraint") {
			return fmt.Errorf("collection with name %s already exists", c.Name)
		}
		return fmt.Errorf("failed to insert collection: %w", err)
	}

	// Create the document table for the collection. It will only be created if
	// it doesn't already exist.
	err = createDocumentTable(ctx, c.db, c.TableName, c.EmbeddingDimensions)
	if err != nil {
		return fmt.Errorf("failed to create document table: %w", err)
	}

	return nil
}

// Update updates a collection in the collections table.
func (c *DocumentCollection) Update(
	ctx context.Context,
) error {
	if c.Name == "" {
		return errors.New("collection Name is required")
	}
	r, err := c.db.NewUpdate().
		Model(c).
		ModelTableExpr("document_collection").
		Where("name = ?", c.Name).
		OmitZero().
		Returning("*").
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to update collection: %w", err)
	}

	// check if no rows were updated
	rowsUpdated, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to check rows affected: %w", err)
	}
	if rowsUpdated == 0 {
		return models.NewNotFoundError("collection: " + c.Name)
	}
	return nil
}

// GetByName returns a collection from the collections table by name.
func (c *DocumentCollection) GetByName(
	ctx context.Context,
) error {
	if c.Name == "" {
		return errors.New("collection name is required")
	}
	err := c.db.NewSelect().
		Model(c).
		ModelTableExpr("document_collection").
		Where("name = ?", c.Name).
		Scan(ctx)
	if err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	if c.UUID == uuid.Nil {
		return models.NewNotFoundError("collection: " + c.Name)
	}
	return nil
}

// GetAll returns a list of all collections from the collections table.
func (c *DocumentCollection) GetAll(
	ctx context.Context,
) ([]models.DocumentCollectionInterface, error) {
	var collections []DocumentCollection
	err := c.db.NewSelect().Model(&collections).ModelTableExpr("document_collection").Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get collection list: %w", err)
	}
	// return slice of interfaces
	var docCollections = make([]models.DocumentCollectionInterface, len(collections))
	for i := range collections {
		docCollections[i] = &collections[i]
	}

	if len(docCollections) == 0 {
		return nil, models.NewNotFoundError("collections")
	}

	return docCollections, nil
}

// Delete deletes a collection from the collections table and drops the
// collection's document table.
func (c *DocumentCollection) Delete(ctx context.Context) error {
	if c.Name == "" {
		return errors.New("collection name is required")
	}
	// start a transaction
	tx, err := c.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	// Get the collection row. If not found, GetByName will return a
	// models.NotFoundError.
	err = c.GetByName(ctx)
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

// CreateDocuments inserts the given documents into the given collection.
func (c *DocumentCollection) CreateDocuments(
	ctx context.Context,
	documents []models.DocumentInterface,
) ([]uuid.UUID, error) {
	if len(documents) == 0 {
		return nil, nil
	}
	if c.Name == "" {
		return nil, errors.New("collection name cannot be empty")
	}
	if err := c.GetByName(ctx); err != nil {
		return nil, fmt.Errorf("failed to get collection %w", err)
	}

	_, err := c.db.NewInsert().
		Model(&documents).
		ModelTableExpr(c.TableName).
		Returning("uuid").
		Exec(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to insert documents: %w", err)
	}

	// return slice of uuids
	uuids := make([]uuid.UUID, len(documents))
	for i, document := range documents {
		doc, ok := document.(*Document)
		if !ok {
			return nil, errors.New("failed to cast document to Document")
		}
		uuids[i] = doc.UUID
	}

	return uuids, nil
}

// UpdateDocuments updates the document_id, metadata, and embedding columns of the
// given documents in the given collection. The documents must have non-nil uuids.
// **IMPORTANT:** We determine which columns to update based on the fields that are
// non-zero in the given documents. This means that all documents must have data
// for the same fields. If a document is missing data for a field, there could be
// data loss.
func (c *DocumentCollection) UpdateDocuments(
	ctx context.Context,
	documents []models.DocumentInterface,
) error {
	if len(documents) == 0 {
		return nil
	}

	// type assert documents to Document and check for nil uuid
	// We also determine which columns to update based on the fields that are
	// non-nil. This means that all documents must have data for the same fields.
	updateDocumentID := false
	updateMetadata := false
	updateEmbedding := false
	docs := make([]Document, len(documents))
	for i, document := range documents {
		doc, ok := document.(*Document)
		if !ok {
			return errors.New("failed to cast document to Document")
		}
		if doc.UUID == uuid.Nil {
			return errors.New("document uuid cannot be nil")
		}
		docs[i] = *doc
		if len(doc.DocumentID) > 0 {
			updateDocumentID = true
		}
		if len(doc.Metadata) > 0 {
			updateMetadata = true
		}
		if len(doc.Embedding) > 0 {
			updateEmbedding = true
		}
	}

	if !updateDocumentID && !updateMetadata && !updateEmbedding {
		return errors.New("no fields to update")
	}

	var columns []string
	if updateDocumentID {
		columns = append(columns, "document_id")
	}
	if updateMetadata {
		columns = append(columns, "metadata")
	}
	if updateEmbedding {
		columns = append(columns, "embedding")
	}

	err := c.GetByName(ctx)
	if err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	r, err := c.db.NewUpdate().
		Model(&docs).
		ModelTableExpr(c.TableName + " AS document").
		Column(columns...).
		Bulk().
		Where("document.uuid = _data.uuid").
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to update documents: %w", err)
	}

	rowsUpdated, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected: %w", err)
	}
	if rowsUpdated == 0 {
		return models.NewNotFoundError("documents")
	}

	return nil
}

// GetDocuments retrieves documents. If `documents` is non-Nil, it will use the document UUIDs to retrieve
// these documents. Otherwise, it will retrieve all documents. If limit is greater than 0, it will
// only retrieve limit many documents.
func (c *DocumentCollection) GetDocuments(
	ctx context.Context,
	limit int,
	uuids []uuid.UUID,
	documentIDs []string,
) ([]models.DocumentInterface, error) {
	if c.Name == "" {
		return nil, errors.New("collection name cannot be empty")
	}

	if len(uuids) != 0 && len(documentIDs) != 0 {
		return nil, errors.New("cannot specify both uuids and documentIDs")
	}

	if err := c.GetByName(ctx); err != nil {
		return nil, fmt.Errorf("failed to get collection: %w", err)
	}

	maxDocuments := len(uuids)
	if limit > 0 && limit > len(uuids) {
		maxDocuments = limit
	}
	documents := make([]Document, maxDocuments)

	query := c.db.NewSelect().
		Model(&documents).
		ModelTableExpr(c.TableName+" AS document").
		Column("uuid", "created_at", "content", "metadata", "document_id").
		// cast the vectors to a float array
		ColumnExpr("embedding::real[]")

	if len(uuids) > 0 {
		query = query.Where("uuid IN (?)", bun.In(uuids))
	} else if len(documentIDs) > 0 {
		query = query.Where("document_id IN (?)", bun.In(documentIDs))
	}
	if limit > 0 {
		query = query.Limit(limit)
	}

	err := query.
		Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get documents: %w", err)
	}

	// return slice of interfaces
	docInterfaces := make([]models.DocumentInterface, len(documents))
	for i := range documents {
		docInterfaces[i] = &documents[i]
	}

	if len(docInterfaces) == 0 {
		return nil, models.NewNotFoundError("documents")
	}
	return docInterfaces, nil
}

// DeleteDocumentsByUUID deletes a single document from a collection in the DB, identified by its UUID.
func (c *DocumentCollection) DeleteDocumentsByUUID(
	ctx context.Context,
	documentUUIDs []uuid.UUID,
) error {
	if c.Name == "" {
		return errors.New("collection name cannot be empty")
	}
	if err := c.GetByName(ctx); err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	r, err := c.db.NewDelete().
		Model(&Document{}).
		ModelTableExpr(c.TableName).
		Where("uuid IN (?)", bun.In(documentUUIDs)).
		// ModelTableExpr isn't being set on the auto-soft Delete in the WHERE clause,
		// so we have to use WhereAllWithDeleted to avoid adding deleted_at Is NOT NULL
		WhereAllWithDeleted().
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("failed to Delete documents: %w", err)
	}

	rowsDeleted, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected: %w", err)
	}

	if rowsDeleted == 0 {
		return models.NewNotFoundError("documents")
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
		return fmt.Errorf("failed to Delete collection: %w", err)
	}
	rows, err := r.RowsAffected()
	if err != nil {
		return fmt.Errorf("failed to get rows affected: %w", err)
	}
	if rows == 0 {
		return models.NewNotFoundError("collection: " + collectionName)
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

func getDocumentUUIDList(documents []models.DocumentInterface) ([]uuid.UUID, error) {
	uuids := make([]uuid.UUID, len(documents))
	for i, v := range documents {
		doc, ok := v.(*Document)
		if !ok {
			return nil, errors.New("failed to cast document to Document")
		}
		if doc.UUID == uuid.Nil {
			continue
		}
		uuids[i] = doc.UUID
	}
	return uuids, nil
}
