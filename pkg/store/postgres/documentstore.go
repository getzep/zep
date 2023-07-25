package postgres

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/getzep/zep/pkg/store"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
)

// NewDocumentStore returns a new DocumentStore. Use this to correctly initialize the store.
func NewDocumentStore(
	appState *models.AppState,
	client *bun.DB,
	docEmbedTaskCh chan<- []models.DocEmbeddingTask,
) (*DocumentStore, error) {
	if appState == nil {
		return nil, errors.New("nil appState received")
	}

	// Create context that we'll use to shut down the document embedding updater
	ctx, done := context.WithCancel(context.Background())
	// limit the size of the update channel to 100. The updater will block
	// so we don't overwhelm the database.
	docEmbedUpdateCh := make(<-chan []models.DocEmbeddingUpdate, 100)
	pds := &DocumentStore{
		store.BaseDocumentStore[*bun.DB]{Client: client},
		appState,
		docEmbedTaskCh,
		docEmbedUpdateCh,
		done,
	}

	err := pds.OnStart(ctx)

	if err != nil {
		return nil, fmt.Errorf("failed to run OnInit %w", err)
	}
	return pds, nil
}

// Force compiler to validate that DocumentStore implements the DocumentStore interface.
var _ models.DocumentStore[*bun.DB] = &DocumentStore{}

type DocumentStore struct {
	store.BaseDocumentStore[*bun.DB]
	appState        *models.AppState
	docEmbedTaskCh  chan<- []models.DocEmbeddingTask
	DocUpdateTaskCh <-chan []models.DocEmbeddingUpdate
	done            context.CancelFunc
}

func (pds *DocumentStore) OnStart(
	ctx context.Context,
) error {
	// start the document embedding updater in a goroutine
	go func() {
		err := pds.documentEmbeddingUpdater(ctx, pds.DocUpdateTaskCh)
		if err != nil {
			log.Fatalf("failed to start document embedding updater: %v", err)
		}
	}()

	return nil
}

func (pds *DocumentStore) Shutdown(_ context.Context) error {
	pds.done()
	close(pds.docEmbedTaskCh)
	return nil
}

func (pds *DocumentStore) GetClient() *bun.DB {
	return pds.Client
}

func (pds *DocumentStore) CreateCollection(
	ctx context.Context,
	collection models.DocumentCollection,
) error {
	dbCollection := NewDocumentCollectionDAO(pds.appState, pds.Client, collection)

	dbCollection.db = pds.Client
	err := dbCollection.Create(ctx)
	if err != nil {
		return fmt.Errorf("failed to Create collection: %w", err)
	}
	return nil
}

func (pds *DocumentStore) UpdateCollection(
	ctx context.Context,
	collection models.DocumentCollection,
) error {
	if collection.Name == "" {
		return errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(pds.appState, pds.Client, collection)
	err := dbCollection.Update(ctx)
	if err != nil {
		return fmt.Errorf("failed to Update collection: %w", err)
	}
	return nil
}

func (pds *DocumentStore) GetCollection(
	ctx context.Context,
	collectionName string,
) (models.DocumentCollection, error) {
	if collectionName == "" {
		return models.DocumentCollection{}, errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		pds.appState,
		pds.Client,
		models.DocumentCollection{Name: collectionName},
	)

	err := dbCollection.GetByName(ctx)
	if err != nil {
		if strings.Contains(err.Error(), "no rows in result set") {
			return models.DocumentCollection{}, models.NewNotFoundError(
				"collection: " + collectionName,
			)
		}
		return models.DocumentCollection{}, fmt.Errorf("failed to get collection: %w", err)
	}
	return dbCollection.DocumentCollection, nil
}

func (pds *DocumentStore) GetCollectionList(
	ctx context.Context,
) ([]models.DocumentCollection, error) {
	dbCollection := DocumentCollectionDAO{db: pds.Client}
	dbCollections, err := dbCollection.GetAll(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get collection list: %w", err)
	}

	return dbCollections, nil
}

func (pds *DocumentStore) DeleteCollection(
	ctx context.Context,
	collectionName string,
) error {
	if collectionName == "" {
		return errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		pds.appState,
		pds.Client,
		models.DocumentCollection{Name: collectionName},
	)
	err := dbCollection.Delete(ctx)
	if err != nil {
		return fmt.Errorf("failed to Delete collection: %w", err)
	}
	return nil
}

func (pds *DocumentStore) CreateDocuments(
	ctx context.Context,
	collectionName string,
	documents []models.Document,
) ([]uuid.UUID, error) {
	if collectionName == "" {
		return nil, errors.New("collection name is empty")
	}
	collection := NewDocumentCollectionDAO(
		pds.appState,
		pds.Client,
		models.DocumentCollection{Name: collectionName},
	)

	// determine if the documents include embeddings
	// throw an error if the collection is configured to auto-embed
	// and any of documents include embeddings.
	// similarly, throw an error if the collection is not configured
	// to auto-embed and any of documents is missing embeddings.
	someEmbeddings, someEmpty := false, false
	for i := range documents {
		if len(documents[i].Embedding) == 0 {
			someEmpty = true
		} else {
			someEmbeddings = true
		}
		if someEmbeddings && someEmpty {
			break
		}
	}
	if collection.IsAutoEmbedded && someEmbeddings {
		return nil, errors.New(
			"cannot create documents with embeddings in an auto-embedded collection",
		)
	}
	if !collection.IsAutoEmbedded && someEmpty {
		return nil, errors.New(
			"cannot create documents without embeddings in a non-auto-embedded collection",
		)
	}

	uuids, err := collection.CreateDocuments(ctx, documents)
	if err != nil {
		return nil, fmt.Errorf("failed to Create documents: %w", err)
	}

	return uuids, nil
}

func (pds *DocumentStore) UpdateDocuments(
	ctx context.Context,
	collectionName string,
	documents []models.Document,
) error {
	if collectionName == "" {
		return errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		pds.appState,
		pds.Client,
		models.DocumentCollection{Name: collectionName},
	)
	err := dbCollection.UpdateDocuments(ctx, documents)
	if err != nil {
		return fmt.Errorf("failed to Update documents: %w", err)
	}

	return nil
}

func (pds *DocumentStore) GetDocuments(
	ctx context.Context,
	collectionName string,
	uuids []uuid.UUID,
	documentIDs []string,
) ([]models.Document, error) {
	if collectionName == "" {
		return nil, errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		pds.appState,
		pds.Client,
		models.DocumentCollection{Name: collectionName},
	)
	documents, err := dbCollection.GetDocuments(ctx, 0, uuids, documentIDs)
	if err != nil {
		return nil, fmt.Errorf("failed to get document: %w", err)
	}

	return documents, nil
}

func (pds *DocumentStore) DeleteDocuments(
	ctx context.Context,
	collectionName string,
	documentUUID []uuid.UUID,
) error {
	if collectionName == "" {
		return errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		pds.appState,
		pds.Client,
		models.DocumentCollection{Name: collectionName},
	)
	err := dbCollection.DeleteDocumentsByUUID(ctx, documentUUID)
	if err != nil {
		return fmt.Errorf("failed to delete document: %w", err)
	}

	return nil
}

func (pds *DocumentStore) SearchCollection(
	ctx context.Context,
	query *models.DocumentSearchPayload,
	limit int,
	mmr bool,
	pageNumber int,
	pageSize int,
) ([]models.DocumentSearchResultPage, error) {
	return nil, errors.New("not implemented")
}

func (pds *DocumentStore) documentEmbeddingUpdater(
	ctx context.Context,
	updateReceiver <-chan []models.DocEmbeddingUpdate,
) error {
	for {
		select {
		case <-ctx.Done():
			log.Info("document embedding updater shutting down")
			return nil
		case updates := <-updateReceiver:
			dbCollection := NewDocumentCollectionDAO(
				pds.appState,
				pds.Client,
				// TODO: this assumption is hacky. Fix this.
				models.DocumentCollection{Name: updates[0].CollectionName},
			)
			docs := documentsFromEmbeddingUpdates(updates)
			err := dbCollection.UpdateDocuments(ctx, docs)
			if err != nil {
				return fmt.Errorf("failed to update document embedding: %w", err)
			}
		}
	}
}

func documentsFromEmbeddingUpdates(updates []models.DocEmbeddingUpdate) []models.Document {
	docs := make([]models.Document, len(updates))
	for i := range updates {
		d := models.Document{
			DocumentBase: models.DocumentBase{
				UUID: updates[i].UUID,
			},
			Embedding: updates[i].Embedding,
		}
		docs[i] = d
	}
	return docs
}
