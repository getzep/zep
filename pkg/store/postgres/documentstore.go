package postgres

import (
	"context"
	"errors"

	"github.com/getzep/zep/pkg/store"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
)

// NewDocumentStore returns a new DocumentStore. Use this to correctly initialize the store.
func NewDocumentStore(
	appState *models.AppState,
	client *bun.DB,
) (*DocumentStore, error) {
	if appState == nil {
		return nil, store.NewStorageError("nil appState received", nil)
	}

	pds := &DocumentStore{store.BaseDocumentStore[*bun.DB]{Client: client}}
	err := pds.OnStart(context.Background(), appState)
	if err != nil {
		return nil, store.NewStorageError("failed to run OnInit", err)
	}
	return pds, nil
}

// Force compiler to validate that PostgresMemoryStore implements the MemoryStore interface.
var _ models.DocumentStore[*bun.DB] = &DocumentStore{}

type DocumentStore struct {
	store.BaseDocumentStore[*bun.DB]
}

func (pds *DocumentStore) OnStart(
	_ context.Context,
	appState *models.AppState,
) error {
	return nil
}

func (pds *DocumentStore) GetClient() *bun.DB {
	return pds.Client
}

func (pds *DocumentStore) PutCollection(
	ctx context.Context,
	collection models.DocumentCollectionInterface,
) error {
	dbCollection, ok := collection.(*DocumentCollection)
	if !ok {
		return store.NewStorageError("failed to type assert document", nil)
	}
	dbCollection.db = pds.Client
	err := dbCollection.Put(ctx)
	if err != nil {
		return store.NewStorageError("failed to Put collection", err)
	}
	return nil
}

func (pds *DocumentStore) GetCollection(
	ctx context.Context,
	collectionName string,
) (models.DocumentCollectionInterface, error) {
	if collectionName == "" {
		return nil, store.NewStorageError("collection name is empty", nil)
	}
	dbCollection := DocumentCollection{Name: collectionName, db: pds.Client}
	err := dbCollection.GetByName(ctx)
	if err != nil {
		return nil, store.NewStorageError("failed to get collection", err)
	}
	return &dbCollection, nil
}

func (pds *DocumentStore) GetCollectionList(
	ctx context.Context,
) ([]models.DocumentCollectionInterface, error) {
	dbCollection := DocumentCollection{db: pds.Client}
	dbCollections, err := dbCollection.GetAll(ctx)
	if err != nil {
		return nil, store.NewStorageError("failed to get collection list", err)
	}

	collections := make([]models.DocumentCollectionInterface, len(dbCollections))
	for i, dbCollection := range dbCollections {
		collections[i] = dbCollection
	}
	return collections, nil
}

func (pds *DocumentStore) DeleteCollection(
	ctx context.Context,
	collectionName string,
) error {
	if collectionName == "" {
		return store.NewStorageError("collection name is empty", nil)
	}
	dbCollection := DocumentCollection{Name: collectionName, db: pds.Client}
	err := dbCollection.Delete(ctx)
	if err != nil {
		return store.NewStorageError("failed to Delete collection", err)
	}
	return nil
}

func (pds *DocumentStore) PutDocuments(
	ctx context.Context,
	collectionName string,
	documents []models.DocumentInterface,
) error {
	if collectionName == "" {
		return store.NewStorageError("collection name is empty", nil)
	}
	dbCollection := DocumentCollection{Name: collectionName, db: pds.Client}
	err := dbCollection.PutDocuments(ctx, documents)
	if err != nil {
		return store.NewStorageError("failed to Put documents", err)
	}

	return nil
}

func (pds *DocumentStore) GetDocuments(
	ctx context.Context,
	collectionName string,
	uuids []uuid.UUID,
) ([]models.DocumentInterface, error) {
	if collectionName == "" {
		return nil, store.NewStorageError("collection name is empty", nil)
	}

	dbCollection := DocumentCollection{Name: collectionName, db: pds.Client}
	documents, err := dbCollection.GetDocuments(ctx, 0, uuids)
	if err != nil {
		return nil, store.NewStorageError("failed to get document", err)
	}

	return documents, nil
}

func (pds *DocumentStore) DeleteDocument(
	ctx context.Context,
	collectionName string,
	documentUUID uuid.UUID,
) error {
	if collectionName == "" {
		return store.NewStorageError("collection name is empty", nil)
	}
	dbCollection := DocumentCollection{Name: collectionName, db: pds.Client}
	err := dbCollection.DeleteDocumentByUUID(ctx, documentUUID)
	if err != nil {
		return store.NewStorageError("failed to delete document", err)
	}

	return nil
}

func (pds *DocumentStore) PutDocumentEmbeddings(
	ctx context.Context,
	collectionName string,
	documents []models.DocumentInterface,
) error {
	if collectionName == "" {
		return store.NewStorageError("collection name is empty", nil)
	}
	dbCollection := DocumentCollection{Name: collectionName, db: pds.Client}
	err := dbCollection.PutDocumentEmbeddings(ctx, documents)
	if err != nil {
		return store.NewStorageError("failed to put document embeddings", err)
	}

	return nil
}

func (pds *DocumentStore) SearchCollection(
	ctx context.Context,
	appState *models.AppState,
	collectionName string,
	query *models.DocumentSearchPayload,
	limit int,
	mmr bool,
	pageNumber int,
	pageSize int,
) ([]models.DocumentSearchResultPage, error) {
	return nil, errors.New("not implemented")
}
