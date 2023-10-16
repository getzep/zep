package postgres

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/getzep/zep/pkg/store"

	"github.com/getzep/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/uptrace/bun"
	"github.com/uptrace/bun/driver/pgdriver"
)

func NewDocumentCollectionDAO(
	appState *models.AppState,
	db *bun.DB,
	collection models.DocumentCollection,
) *DocumentCollectionDAO {
	return &DocumentCollectionDAO{appState: appState, db: db, DocumentCollection: collection}
}

type DocumentCollectionDAO struct {
	appState *models.AppState
	db       *bun.DB `bun:"-"`
	models.DocumentCollection
}

// Create inserts a collection into the collections table and creates a
// table for the collection's documents.
func (dc *DocumentCollectionDAO) Create(
	ctx context.Context,
) error {
	// Ensure that the collection name is lowercase.
	dc.Name = dc.getName()

	// TODO: validate collection struct fields using validator
	if dc.getName() == "" {
		return errors.New("collection name is required")
	}

	if dc.TableName == "" {
		tableName, err := generateDocumentTableName(dc)
		if err != nil {
			return fmt.Errorf("failed to generate collection table name: %w", err)
		}
		dc.TableName = tableName
	}

	// Determine the index type to use. Default to HNSW if available, otherwise
	// use IVFFLAT.
	dc.IndexType = "ivfflat"
	if dc.appState.Config.Store.Postgres.AvailableIndexes.HSNW {
		dc.IndexType = "hnsw"
		// We'll create the index when we create the document table.
		dc.IsIndexed = true
	}

	// We only support cosine distance function for now.
	dc.DistanceFunction = "cosine"

	collectionRecord := DocumentCollectionSchema{DocumentCollection: dc.DocumentCollection}

	_, err := dc.db.NewInsert().
		Model(&collectionRecord).
		Returning("*").
		Exec(ctx)
	if err != nil {
		if err, ok := err.(pgdriver.Error); ok && err.IntegrityViolation() {
			return models.NewBadRequestError("collection already exists: " + dc.getName())
		}
		return fmt.Errorf("failed to insert collection: %w", err)
	}

	// Create the document table for the collection. It will only be created if
	// it doesn't already exist.
	err = createDocumentTable(ctx, dc.appState, dc.db, dc.TableName, dc.EmbeddingDimensions)
	if err != nil {
		return fmt.Errorf("failed to create document table: %w", err)
	}

	return nil
}

// Update updates a collection in the collections table.
func (dc *DocumentCollectionDAO) Update(
	ctx context.Context,
) error {
	if dc.getName() == "" {
		return errors.New("collection Name is required")
	}

	collectionRecord := DocumentCollectionSchema{DocumentCollection: dc.DocumentCollection}

	r, err := dc.db.NewUpdate().
		Model(&collectionRecord).
		Where("name = ?", dc.getName()).
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
		return models.NewNotFoundError("collection: " + dc.getName())
	}
	return nil
}

// GetByName returns a collection from the collections table by name.
func (dc *DocumentCollectionDAO) GetByName(
	ctx context.Context,
) error {
	if dc.getName() == "" {
		return errors.New("collection name is required")
	}

	collectionRecord := DocumentCollectionSchema{DocumentCollection: dc.DocumentCollection}

	err := dc.db.NewSelect().
		Model(&collectionRecord).
		Where("name = ?", dc.getName()).
		Scan(ctx)
	if err != nil {
		if strings.Contains(err.Error(), "no rows in result set") {
			return models.NewNotFoundError("collection: " + dc.getName())
		}
		return fmt.Errorf("failed to get collection: %w", err)
	}

	if collectionRecord.UUID == uuid.Nil {
		return models.NewNotFoundError("collection: " + dc.getName())
	}

	dc.DocumentCollection = collectionRecord.DocumentCollection

	counts, err := dc.GetCollectionCounts(ctx)
	if err != nil {
		return fmt.Errorf("failed to get collection counts: %w", err)
	}

	dc.DocumentCollection.DocumentCollectionCounts = &counts

	return nil
}

// GetAll returns a list of all collections from the collections table.
func (dc *DocumentCollectionDAO) GetAll(
	ctx context.Context,
) ([]models.DocumentCollection, error) {
	var collections []models.DocumentCollection
	err := dc.db.NewSelect().Model(&collections).ModelTableExpr("document_collection").Scan(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get collection list: %w", err)
	}

	for i := range collections {
		c := NewDocumentCollectionDAO(dc.appState, dc.db, collections[i])
		err = c.GetByName(ctx)
		if err != nil {
			return nil, fmt.Errorf("failed to get collection: %w", err)
		}
		counts, err := c.GetCollectionCounts(ctx)
		if err != nil {
			return nil, fmt.Errorf("failed to get collection counts: %w", err)
		}
		collections[i].DocumentCollectionCounts = &counts
	}

	return collections, nil
}

// GetCollectionCounts returns the number of documents and embedded documents
func (dc *DocumentCollectionDAO) GetCollectionCounts(
	ctx context.Context,
) (models.DocumentCollectionCounts, error) {
	if dc.TableName == "" {
		return models.DocumentCollectionCounts{}, errors.New("collection TableName is required")
	}
	counts := models.DocumentCollectionCounts{}

	err := dc.db.NewSelect().
		Model(&counts).
		ModelTableExpr("?", bun.Ident(dc.TableName)).
		ColumnExpr("count(*) as document_count").
		ColumnExpr("COUNT(*) FILTER (WHERE is_embedded) as document_embedded_count").
		Scan(ctx)
	if err != nil {
		return models.DocumentCollectionCounts{}, fmt.Errorf(
			"failed to get collection counts: %w",
			err,
		)
	}

	return counts, nil
}

// Delete deletes a collection from the collections table and drops the
// collection's document table.
func (dc *DocumentCollectionDAO) Delete(ctx context.Context) error {
	if dc.getName() == "" {
		return errors.New("collection name is required")
	}
	// start a transaction
	tx, err := dc.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("failed to begin transaction: %w", err)
	}
	defer rollbackOnError(tx)

	// Get the collection row. If not found, GetByName will return a
	// models.NotFoundError.
	err = dc.GetByName(ctx)
	if err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	// Delete the collection row.
	err = deleteCollectionRow(ctx, tx, dc.getName())
	if err != nil {
		return err
	}

	// Drop the document table for the collection.
	err = dropDocumentTable(ctx, tx, dc.TableName)
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
func (dc *DocumentCollectionDAO) CreateDocuments(
	ctx context.Context,
	documents []models.Document,
) ([]uuid.UUID, error) {
	if len(documents) == 0 {
		return nil, nil
	}
	if dc.getName() == "" {
		return nil, errors.New("collection name cannot be empty")
	}
	if err := dc.GetByName(ctx); err != nil {
		return nil, fmt.Errorf("failed to get collection %w", err)
	}

	// if the collection is not auto embedded, then we must have been given embeddings
	// if we got this far. Set the IsEmbedded flag to true for all documents.
	if !dc.IsAutoEmbedded {
		for i := range documents {
			documents[i].IsEmbedded = true
		}
	}
	_, err := dc.db.NewInsert().
		Model(&documents).
		ModelTableExpr("?", bun.Ident(dc.TableName)).
		Returning("uuid").
		Exec(ctx)
	if err != nil {
		if err, ok := err.(pgdriver.Error); ok && err.IntegrityViolation() {
			return nil, models.NewBadRequestError("document_id already exists")
		}
		if strings.Contains(err.Error(), "different vector dimensions") {
			return nil, store.NewEmbeddingMismatchError(err)
		}
		return nil, fmt.Errorf("failed to insert documents: %w", err)
	}

	uuids := make([]uuid.UUID, len(documents))
	for i := range documents {
		uuids[i] = documents[i].UUID
	}

	return uuids, nil
}

// UpdateDocuments updates the document_id, metadata, and embedding columns of the
// given documents in the given collection. The documents must have non-nil uuids.
//
// **IMPORTANT:** We determine which columns to update based on the fields that are
// non-zero in the given documents. This means that all documents must have data
// for the same fields. If a document is missing data for a field, there could be
// data loss.
func (dc *DocumentCollectionDAO) UpdateDocuments(
	ctx context.Context,
	documents []models.Document,
) error {
	if len(documents) == 0 {
		return nil
	}

	// Check for nil uuids.
	// We also determine which columns to update based on the fields that are
	// non-nil. This means that all documents must have data for the same fields.
	updateDocumentID := false
	updateMetadata := false
	updateEmbedding := false
	for i := range documents {
		document := &documents[i]
		if document.UUID == uuid.Nil {
			return errors.New("document uuid cannot be nil")
		}
		if len(document.DocumentID) > 0 {
			updateDocumentID = true
		}
		if len(document.Metadata) > 0 {
			updateMetadata = true
		}
		if len(document.Embedding) > 0 {
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
		columns = append(columns, "embedding", "is_embedded")
	}

	err := dc.GetByName(ctx)
	if err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	r, err := dc.db.NewUpdate().
		Model(&documents).
		ModelTableExpr("? AS document", bun.Ident(dc.TableName)).
		Column(columns...).
		Bulk().
		Where("document.uuid = _data.uuid").
		Exec(ctx)
	if err != nil {
		if strings.Contains(err.Error(), "different vector dimensions") {
			return store.NewEmbeddingMismatchError(err)
		}
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
func (dc *DocumentCollectionDAO) GetDocuments(
	ctx context.Context,
	limit int,
	uuids []uuid.UUID,
	documentIDs []string,
) ([]models.Document, error) {
	if dc.getName() == "" {
		return nil, errors.New("collection name cannot be empty")
	}

	if len(uuids) != 0 && len(documentIDs) != 0 {
		return nil, errors.New("cannot specify both uuids and documentIDs")
	}

	if err := dc.GetByName(ctx); err != nil {
		return nil, fmt.Errorf("failed to get collection: %w", err)
	}

	maxDocuments := len(uuids)
	if limit > 0 && limit > len(uuids) {
		maxDocuments = limit
	}
	documents := make([]models.Document, maxDocuments)

	query := dc.db.NewSelect().
		Model(&documents).
		ModelTableExpr("? AS document", bun.Ident(dc.TableName)).
		Column("uuid", "created_at", "content", "metadata", "document_id", "embedding", "is_embedded")

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

	if len(documents) == 0 {
		return nil, models.NewNotFoundError("documents")
	}
	return documents, nil
}

// DeleteDocumentsByUUID deletes a single document from a collection in the SqlDB, identified by its UUID.
func (dc *DocumentCollectionDAO) DeleteDocumentsByUUID(
	ctx context.Context,
	documentUUIDs []uuid.UUID,
) error {
	if dc.getName() == "" {
		return errors.New("collection name cannot be empty")
	}
	if err := dc.GetByName(ctx); err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	r, err := dc.db.NewDelete().
		Model(&models.Document{}).
		ModelTableExpr("?", bun.Ident(dc.TableName)).
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

// SearchDocuments searches for documents in a collection. Currently pagination is not supported.
func (dc *DocumentCollectionDAO) SearchDocuments(ctx context.Context,
	query *models.DocumentSearchPayload,
	limit int,
	pageNumber int,
	pageSize int) (*models.DocumentSearchResultPage, error) {
	// TODO: implement pagination
	_ = pageNumber
	_ = pageSize

	if dc.getName() == "" {
		return nil, errors.New("collection name cannot be empty")
	}

	if len(query.Text) == 0 && len(query.Metadata) == 0 && len(query.Embedding) == 0 {
		return nil, errors.New("at least one of text, metadata, or embedding must be specified")
	}

	if len(query.Text) > 0 && len(query.Embedding) > 0 {
		return nil, errors.New("cannot specify both text and embedding")
	}

	if err := dc.GetByName(ctx); err != nil {
		return nil, fmt.Errorf("failed to get collection: %w", err)
	}

	search := newDocumentSearchOperation(
		ctx,
		dc.appState,
		dc.db,
		query,
		&dc.DocumentCollection,
		limit,
	)

	results, err := search.Execute()
	if err != nil {
		return nil, fmt.Errorf("failed to execute search: %w", err)
	}

	return results, nil
}

func (dc *DocumentCollectionDAO) getName() string {
	return strings.ToLower(dc.Name)
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
// collection name to 47 characters.
// We also need to sanitize the collection name.
func generateDocumentTableName(collection *DocumentCollectionDAO) (string, error) {
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
