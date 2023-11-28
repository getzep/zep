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

const DefaultDocEmbeddingChunkSize = 1000

// NewDocumentStore returns a new DocumentStore. Use this to correctly initialize the store.
func NewDocumentStore(
	ctx context.Context,
	appState *models.AppState,
	client *bun.DB,
) (*DocumentStore, error) {
	if appState == nil {
		return nil, errors.New("nil appState received")
	}

	ds := &DocumentStore{
		store.BaseDocumentStore[*bun.DB]{Client: client},
		appState,
	}

	err := ds.OnStart(ctx)

	if err != nil {
		return nil, fmt.Errorf("failed to run OnInit %w", err)
	}
	return ds, nil
}

var _ models.DocumentStore[*bun.DB] = &DocumentStore{}

type DocumentStore struct {
	store.BaseDocumentStore[*bun.DB]
	appState *models.AppState
}

func (ds *DocumentStore) OnStart(
	_ context.Context,
) error {
	return nil
}

func (ds *DocumentStore) Shutdown(_ context.Context) error {
	return nil
}

func (ds *DocumentStore) GetClient() any {
	return ds.Client
}

func (ds *DocumentStore) CreateCollection(
	ctx context.Context,
	collection models.DocumentCollection,
) error {
	dbCollection := NewDocumentCollectionDAO(ds.appState, ds.Client, collection)

	dbCollection.db = ds.Client
	err := dbCollection.Create(ctx)
	if err != nil {
		return fmt.Errorf("failed to create collection: %w", err)
	}
	return nil
}

func (ds *DocumentStore) UpdateCollection(
	ctx context.Context,
	collection models.DocumentCollection,
) error {
	if collection.Name == "" {
		return errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(ds.appState, ds.Client, collection)
	err := dbCollection.Update(ctx)
	if err != nil {
		return fmt.Errorf("failed to update collection: %w", err)
	}
	return nil
}

func (ds *DocumentStore) GetCollection(
	ctx context.Context,
	collectionName string,
) (models.DocumentCollection, error) {
	if collectionName == "" {
		return models.DocumentCollection{}, errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		ds.appState,
		ds.Client,
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

func (ds *DocumentStore) GetCollectionList(
	ctx context.Context,
) ([]models.DocumentCollection, error) {
	dbCollection := DocumentCollectionDAO{db: ds.Client}
	dbCollections, err := dbCollection.GetAll(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get collection list: %w", err)
	}

	return dbCollections, nil
}

func (ds *DocumentStore) DeleteCollection(
	ctx context.Context,
	collectionName string,
) error {
	if collectionName == "" {
		return errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		ds.appState,
		ds.Client,
		models.DocumentCollection{Name: collectionName},
	)
	err := dbCollection.Delete(ctx)
	if err != nil {
		return fmt.Errorf("failed to Delete collection: %w", err)
	}
	return nil
}

func (ds *DocumentStore) CreateDocuments(
	ctx context.Context,
	collectionName string,
	documents []models.Document,
) ([]uuid.UUID, error) {
	if collectionName == "" {
		return nil, errors.New("collection name is empty")
	}
	collection := NewDocumentCollectionDAO(
		ds.appState,
		ds.Client,
		models.DocumentCollection{Name: collectionName},
	)

	err := collection.GetByName(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get collection: %w", err)
	}

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
		return nil, models.NewBadRequestError(
			"cannot create documents without embeddings in a non-auto-embedded collection",
		)
	}

	uuids, err := collection.CreateDocuments(ctx, documents)
	if err != nil {
		return nil, fmt.Errorf("failed to create documents: %w", err)
	}

	// if the collection is configured to auto-embed, send the documents
	// to the document embedding tasker
	if collection.IsAutoEmbedded {
		ds.documentEmbeddingTasker(collectionName, documents)
	}

	return uuids, nil
}

func (ds *DocumentStore) UpdateDocuments(
	ctx context.Context,
	collectionName string,
	documents []models.Document,
) error {
	if collectionName == "" {
		return errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		ds.appState,
		ds.Client,
		models.DocumentCollection{Name: collectionName},
	)
	err := dbCollection.UpdateDocuments(ctx, documents)
	if err != nil {
		return fmt.Errorf("failed to Update documents: %w", err)
	}

	return nil
}

func (ds *DocumentStore) GetDocuments(
	ctx context.Context,
	collectionName string,
	uuids []uuid.UUID,
	documentIDs []string,
) ([]models.Document, error) {
	if collectionName == "" {
		return nil, errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		ds.appState,
		ds.Client,
		models.DocumentCollection{Name: collectionName},
	)
	documents, err := dbCollection.GetDocuments(ctx, 0, uuids, documentIDs)
	if err != nil {
		return nil, fmt.Errorf("failed to get documents: %w", err)
	}

	return documents, nil
}

func (ds *DocumentStore) DeleteDocuments(
	ctx context.Context,
	collectionName string,
	documentUUID []uuid.UUID,
) error {
	if collectionName == "" {
		return errors.New("collection name is empty")
	}
	dbCollection := NewDocumentCollectionDAO(
		ds.appState,
		ds.Client,
		models.DocumentCollection{Name: collectionName},
	)
	err := dbCollection.DeleteDocumentsByUUID(ctx, documentUUID)
	if err != nil {
		return fmt.Errorf("failed to delete document: %w", err)
	}

	return nil
}

func (ds *DocumentStore) SearchCollection(
	ctx context.Context,
	query *models.DocumentSearchPayload,
	limit int,
	pageNumber int,
	pageSize int,
) (*models.DocumentSearchResultPage, error) {
	collectionDAO := NewDocumentCollectionDAO(
		ds.appState,
		ds.Client,
		models.DocumentCollection{Name: query.CollectionName},
	)

	results, err := collectionDAO.SearchDocuments(ctx, query, limit, pageNumber, pageSize)
	if err != nil {
		return nil, fmt.Errorf("failed to search collection: %w", err)
	}

	return results, nil
}

func (ds *DocumentStore) CreateCollectionIndex(
	ctx context.Context,
	collectionName string,
	force bool,
) error {
	collection := NewDocumentCollectionDAO(
		ds.appState,
		ds.Client,
		models.DocumentCollection{Name: collectionName},
	)

	err := collection.GetByName(ctx)
	if err != nil {
		return fmt.Errorf("failed to get collection: %w", err)
	}

	if collection.IndexType != "ivfflat" {
		log.Warningf(
			"collection %s is of type %s, which is not supported for manual indexing",
			collection.Name,
			collection.IndexType,
		)
		return nil
	}

	vci, err := NewVectorColIndex(ctx, ds.appState, collection.DocumentCollection)
	if err != nil {
		return fmt.Errorf("failed to create vector column index: %w", err)
	}

	// use the default MinRows value
	err = vci.CreateIndex(ctx, force)
	if err != nil {
		return fmt.Errorf("failed to create index: %w", err)
	}

	return nil
}

func (ds *DocumentStore) documentEmbeddingTasker(
	collectionName string,
	documents []models.Document,
) {
	tasks := make([]models.DocEmbeddingTask, len(documents))
	for i := range documents {
		tasks[i] = models.DocEmbeddingTask{
			UUID: documents[i].UUID,
		}
	}

	// chunk the tasks into groups of taskChunkSize
	taskChunkSize := DefaultDocEmbeddingChunkSize
	tmpChunkSize := ds.appState.Config.Extractors.Documents.Embeddings.ChunkSize
	if tmpChunkSize > 0 {
		taskChunkSize = tmpChunkSize
	}
	taskChunks := chunkTasks(tasks, taskChunkSize)

	for _, taskChunk := range taskChunks {
		err := ds.appState.TaskPublisher.Publish(
			"document_embedder",
			map[string]string{
				"collection_name": collectionName,
			},
			taskChunk,
		)
		if err != nil {
			log.Errorf("failed to publish document embedding task: %v", err)
		}
	}
}

// chunkTasks splits the given tasks into chunks of the given size.
func chunkTasks(tasks []models.DocEmbeddingTask, chunkSize int) [][]models.DocEmbeddingTask {
	var chunks [][]models.DocEmbeddingTask
	for i := 0; i < len(tasks); i += chunkSize {
		end := i + chunkSize
		if end > len(tasks) {
			end = len(tasks)
		}
		chunks = append(chunks, tasks[i:end])
	}
	return chunks
}
