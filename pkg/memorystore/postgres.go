package memorystore

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"math"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
	"github.com/jinzhu/copier"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

// NewPostgresMemoryStore returns a new PostgresMemoryStore. Use this to correctly initialize the store.
func NewPostgresMemoryStore(
	appState *models.AppState,
	client *bun.DB,
) (*PostgresMemoryStore, error) {
	if appState == nil {
		return nil, NewStorageError("nil appState received", nil)
	}

	pms := &PostgresMemoryStore{BaseMemoryStore[*bun.DB]{Client: client}}
	err := pms.OnStart(context.Background(), appState)
	if err != nil {
		return nil, NewStorageError("failed to run OnInit", err)
	}
	return pms, nil
}

// Force compiler to validate that PostgresMemoryStore implements the MemoryStore interface.
var _ models.MemoryStore[*bun.DB] = &PostgresMemoryStore{}

type PostgresMemoryStore struct {
	BaseMemoryStore[*bun.DB]
}

func (pms *PostgresMemoryStore) OnStart(
	_ context.Context,
	_ *models.AppState,
) error {
	err := ensurePostgresSetup(context.Background(), pms.Client)
	if err != nil {
		return NewStorageError("failed to ensure postgres schema setup", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) GetClient() *bun.DB {
	return pms.Client
}

// GetMemory returns the most recent Summary and a list of messages for a given sessionID.
// GetMemory returns:
//   - the most recent Summary, if one exists
//   - the lastNMessages messages, if lastNMessages > 0
//   - all messages since the last SummaryPoint, if lastNMessages == 0
//   - if no Summary (and no SummaryPoint) exists and lastNMessages == 0, returns
//     all undeleted messages
func (pms *PostgresMemoryStore) GetMemory(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	lastNMessages int,
) (*models.Memory, error) {
	if appState == nil {
		return nil, NewStorageError("nil appState received", nil)
	}

	err := checkLastNParms(0, lastNMessages)
	if err != nil {
		return nil, NewStorageError("invalid lastNMessages or lastNTokens in get call", err)
	}

	// Get the most recent summary
	summary, err := getSummary(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, NewStorageError("failed to get summary", err)
	}
	if summary != nil {
		log.Debugf("Got summary for %s: %s", sessionID, summary.UUID)
	}

	messages, err := getMessages(
		ctx,
		pms.Client,
		sessionID,
		appState.Config.Memory.MessageWindow,
		summary,
		lastNMessages,
	)
	if err != nil {
		return nil, NewStorageError("failed to get messages", err)
	}
	if messages != nil {
		log.Debugf("Got messages for %s: %d", sessionID, len(messages))
	}

	memory := models.Memory{
		Messages: messages,
		Summary:  summary,
	}

	return &memory, nil
}

func (pms *PostgresMemoryStore) GetSummary(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
) (*models.Summary, error) {
	summary, err := getSummary(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, NewStorageError("failed to get summary", err)
	}

	return summary, nil
}

func (pms *PostgresMemoryStore) PutMemory(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	memoryMessages *models.Memory,
	skipNotify bool,
) error {
	if appState == nil {
		return NewStorageError("nil appState received", nil)
	}

	messageResult, err := putMessages(
		ctx,
		pms.Client,
		sessionID,
		memoryMessages.Messages,
	)
	if err != nil {
		return NewStorageError("failed to put messages", err)
	}

	if skipNotify {
		return nil
	}

	pms.NotifyExtractors(
		context.Background(),
		appState,
		&models.MessageEvent{SessionID: sessionID,
			Messages: messageResult},
	)

	return nil
}

func (pms *PostgresMemoryStore) PutSummary(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
	summary *models.Summary,
) error {
	_, err := putSummary(ctx, pms.Client, sessionID, summary)
	if err != nil {
		return NewStorageError("failed to put summary", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) SearchMemory(
	ctx context.Context,
	appState *models.AppState,
	sessionID string,
	query *models.SearchPayload,
	limit int,
) ([]models.SearchResult, error) {
	searchResults, err := searchMessages(ctx, appState, pms.Client, sessionID, query, limit)
	return searchResults, err
}

func (pms *PostgresMemoryStore) Close() error {
	if pms.Client != nil {
		return pms.Client.Close()
	}
	return nil
}

// DeleteSession deletes a session from the memory store. This is a soft delete.
// TODO: A hard delete will be implemented as an out-of-band process or left to the implementer.
func (pms *PostgresMemoryStore) DeleteSession(ctx context.Context, sessionID string) error {
	return deleteSession(ctx, pms.Client, sessionID)
}

func (pms *PostgresMemoryStore) PutMessageVectors(ctx context.Context,
	_ *models.AppState,
	sessionID string,
	embeddings []models.Embeddings,
) error {
	if embeddings == nil {
		return NewStorageError("nil embeddings received", nil)
	}
	if len(embeddings) == 0 {
		return NewStorageError("no embeddings received", nil)
	}

	err := putEmbeddings(ctx, pms.Client, sessionID, embeddings)
	if err != nil {
		return NewStorageError("failed to put embeddings", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) GetMessageVectors(ctx context.Context,
	_ *models.AppState,
	sessionID string,
) ([]models.Embeddings, error) {
	embeddings, err := getMessageVectors(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, NewStorageError("GetMessageVectors failed to get embeddings", err)
	}

	return embeddings, nil
}

func getMessageVectors(ctx context.Context,
	db *bun.DB,
	sessionID string) ([]models.Embeddings, error) {
	var results []struct {
		PgMessageStore
		PgMessageVectorStore
	}
	// TODO: Check that excluding deleted
	_, err := db.NewSelect().
		Table("message_embedding").
		Join("JOIN message").
		JoinOn("message_embedding.message_uuid = message.uuid").
		ColumnExpr("message.content").
		ColumnExpr("message_embedding.*").
		Where("message_embedding.is_embedded = ?", true).
		Where("message_embedding.session_id = ?", sessionID).
		Exec(ctx, &results)
	if err != nil {
		return nil, NewStorageError("failed to get message vectors", err)
	}

	embeddings := make([]models.Embeddings, len(results))
	for i, vectorStoreRecord := range results {
		embeddings[i] = models.Embeddings{
			Embedding: vectorStoreRecord.Embedding.Slice(),
			TextUUID:  vectorStoreRecord.MessageUUID,
			Text:      vectorStoreRecord.Content,
		}
	}

	return embeddings, nil
}

// putSession stores a new session or updates an existing session with new metadata.
func putSession(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	metadata map[string]interface{},
) (*models.Session, error) {
	if sessionID == "" {
		return nil, NewStorageError("sessionID cannot be empty", nil)
	}
	session := PgSession{SessionID: sessionID, Metadata: metadata}
	_, err := db.NewInsert().
		Model(&session).
		Column("uuid", "session_id", "created_at", "metadata").
		On("CONFLICT (session_id) DO UPDATE").
		Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to put session", err)
	}

	retSession := models.Session{}
	err = copier.Copy(&retSession, &session)
	if err != nil {
		return nil, NewStorageError("failed to copy session", err)
	}

	return &retSession, nil
}

// getSession retrieves a session from the memory store.
func getSession(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
) (*models.Session, error) {
	session := PgSession{}
	err := db.NewSelect().Model(&session).Where("session_id = ?", sessionID).Scan(ctx)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return nil, NewStorageError("failed to get session", err)
	}

	retSession := models.Session{}
	err = copier.Copy(&retSession, &session)
	if err != nil {
		return nil, NewStorageError("failed to copy session", err)
	}

	return &retSession, nil
}

// putMessages stores a new or updates existing messages for a session. Existing
// messages are determined by message UUID. Sessions are created if they do not
// exist.
func putMessages(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	messages []models.Message,
) ([]models.Message, error) {
	if len(messages) == 0 {
		log.Warn("putMessages called with no messages")
		return nil, nil
	}
	log.Debugf("putMessages called for session %s with %d messages", sessionID, len(messages))

	// Create or update a Session
	_, err := putSession(ctx, db, sessionID, nil)
	if err != nil {
		return nil, NewStorageError("failed to put session", err)
	}

	pgMessages := make([]PgMessageStore, len(messages))
	err = copier.Copy(&pgMessages, &messages)
	if err != nil {
		return nil, NewStorageError("failed to copy messages to pgMessages", err)
	}

	for i := range pgMessages {
		pgMessages[i].SessionID = sessionID
	}

	_, err = db.NewInsert().
		Model(&pgMessages).
		Column("id", "created_at", "uuid", "session_id", "role", "content", "token_count", "metadata").
		On("CONFLICT (uuid) DO UPDATE").
		Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to save memories to store", err)
	}

	retMessages := make([]models.Message, len(messages))
	err = copier.Copy(&retMessages, &pgMessages)
	if err != nil {
		return nil, NewStorageError("failed to copy pgMessages to retMessages", err)
	}

	log.Debugf("putMessages completed for session %s with %d messages", sessionID, len(messages))

	return retMessages, nil
}

// putSummary stores a new summary for a session. The recentMessageID is the UUID of the most recent
// message in the session when the summary was created.
func putSummary(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	summary *models.Summary,
) (*models.Summary, error) {
	if sessionID == "" {
		return nil, NewStorageError("sessionID cannot be empty", nil)
	}

	pgSummary := PgSummaryStore{}
	err := copier.Copy(&pgSummary, summary)
	if err != nil {
		return nil, NewStorageError("failed to copy summary", err)
	}

	pgSummary.SessionID = sessionID

	_, err = db.NewInsert().Model(&pgSummary).Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to put summary", err)
	}

	retSummary := models.Summary{}
	err = copier.Copy(&retSummary, &pgSummary)
	if err != nil {
		return nil, NewStorageError("failed to copy summary", err)
	}

	return &retSummary, nil
}

// getMessages retrieves messages from the memory store. If lastNMessages is 0, the last SummaryPoint is retrieved.
func getMessages(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	memoryWindow int,
	summary *models.Summary,
	lastNMessages int,
) ([]models.Message, error) {
	if sessionID == "" {
		return nil, NewStorageError("sessionID cannot be empty", nil)
	}
	if memoryWindow == 0 {
		return nil, NewStorageError("memory.message_window must be greater than 0", nil)
	}

	// if lastNMessages == 0 and summary.MessagePoint != Nil, retrieve the SummaryPoint index
	var summaryPointIndex int64
	var err error
	if lastNMessages == 0 && summary != nil && summary.SummaryPointUUID != uuid.Nil {
		summaryPointIndex, err = getSummaryPointIndex(ctx, db, sessionID, summary.SummaryPointUUID)
		if err != nil {
			return nil, NewStorageError("unable to retrieve summary", nil)
		}
	}

	var messages []PgMessageStore
	query := db.NewSelect().
		Model(&messages).
		Where("session_id = ?", sessionID).
		Order("id DESC")

	if lastNMessages > 0 {
		query.Limit(lastNMessages)
	}

	// Only get messages created after the SummaryPoint if summaryPointIndex != 0
	if summaryPointIndex != 0 {
		query.Where("id > ?", summaryPointIndex)
	}

	err = query.Scan(ctx)
	if err != nil {
		return nil, NewStorageError("failed to get messages", err)
	}

	if len(messages) == 0 {
		return nil, nil
	}

	messageList := make([]models.Message, len(messages))
	err = copier.Copy(&messageList, &messages)
	if err != nil {
		return nil, NewStorageError("failed to copy messages", err)
	}

	return messageList, nil
}

// getSummaryPointIndex retrieves the index of the last summary point for a session
// This is a bit of a hack since UUIDs are not sortable.
// If the SummaryPoint does not exist (for e.g. if it was deleted), returns 0.
func getSummaryPointIndex(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	summaryPointUUID uuid.UUID,
) (int64, error) {
	var message PgMessageStore

	err := db.NewSelect().
		Model(&message).
		Column("id").
		Where("session_id = ? AND uuid = ?", sessionID, summaryPointUUID).
		Where("deleted_at IS NULL").
		Scan(ctx)

	if err != nil {
		if err == sql.ErrNoRows {
			log.Warningf(
				"unable to retrieve last summary point for %s: %s",
				summaryPointUUID,
				err,
			)
		} else {
			return 0, NewStorageError("unable to retrieve last summary point for %s", err)
		}

		return 0, nil
	}

	return message.ID, nil
}

// getSummary returns the most recent summary for a session
func getSummary(ctx context.Context, db *bun.DB, sessionID string) (*models.Summary, error) {
	summary := PgSummaryStore{}
	err := db.NewSelect().
		Model(&summary).
		Where("session_id = ?", sessionID).
		Where("deleted_at IS NULL").
		// Get the most recent summary
		Order("created_at DESC").
		Limit(1).
		Scan(ctx)
	if err != nil {
		if err == sql.ErrNoRows {
			return nil, nil
		}
		return &models.Summary{}, NewStorageError("failed to get session", err)
	}

	respSummary := models.Summary{}
	err = copier.Copy(&respSummary, &summary)
	if err != nil {
		return nil, NewStorageError("failed to copy summary", err)
	}
	return &respSummary, nil
}

func putEmbeddings(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	embeddings []models.Embeddings,
) error {
	if embeddings == nil {
		return NewStorageError("nil embeddings received", nil)
	}
	if len(embeddings) == 0 {
		return NewStorageError("no embeddings received", nil)
	}

	embeddingVectors := make([]PgMessageVectorStore, len(embeddings))
	for i, e := range embeddings {
		embeddingVectors[i] = PgMessageVectorStore{
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
		return NewStorageError("failed to insert message vectors", err)
	}

	return nil
}

func searchMessages(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	sessionID string,
	query *models.SearchPayload,
	limit int,
) ([]models.SearchResult, error) {
	if query == nil {
		return nil, NewStorageError("nil query received", nil)
	}

	s := query.Text
	if s == "" {
		return nil, NewStorageError("empty query", errors.New("empty query"))
	}

	if appState == nil {
		return nil, NewStorageError("nil appState received", nil)
	}

	log.Debugf("searchMessages called for session %s", sessionID)

	if limit == 0 {
		limit = 10
	}

	e, err := llms.EmbedMessages(ctx, appState, []string{s})
	if err != nil {
		return nil, NewStorageError("failed to embed query", err)
	}
	vector := pgvector.NewVector((*e)[0].Embedding)

	var results []models.SearchResult
	err = db.NewSelect().
		TableExpr("message_embedding AS me").
		Join("JOIN message AS m").
		JoinOn("me.message_uuid = m.uuid").
		ColumnExpr("m.uuid AS message__uuid").
		ColumnExpr("m.created_at AS message__created_at").
		ColumnExpr("m.role AS message__role").
		ColumnExpr("m.content AS message__content").
		ColumnExpr("m.metadata AS message__metadata").
		ColumnExpr("m.token_count AS message__token_count").
		ColumnExpr("1 - (embedding <=> ? ) AS dist", vector).
		Where("m.session_id = ?", sessionID).
		Order("dist DESC").
		Limit(limit).
		Scan(ctx, &results)
	if err != nil {
		return nil, NewStorageError("memory searchMessages failed", err)
	}

	// some results may be returned where distance is NaN. This is a race between
	// newly added messages and the search query. We filter these out.
	var filteredResults []models.SearchResult
	for _, result := range results {
		if !math.IsNaN(result.Dist) {
			filteredResults = append(filteredResults, result)
		}
	}
	log.Debugf("searchMessages completed for session %s", sessionID)

	return filteredResults, nil
}

// deleteSession deletes a session from the memory store. This is a soft delete.
// TODO: This is ugly. Determine why bun's cascading deletes aren't working
func deleteSession(ctx context.Context, db *bun.DB, sessionID string) error {
	log.Debugf("deleting from memory store for session %s", sessionID)
	schemas := []bun.BeforeCreateTableHook{
		&PgMessageVectorStore{},
		&PgSummaryStore{},
		&PgMessageStore{},
		&PgSession{},
	}
	for _, schema := range schemas {
		log.Debugf("deleting session %s from schema %v", sessionID, schema)
		_, err := db.NewDelete().
			Model(schema).
			Where("session_id = ?", sessionID).
			Exec(ctx)
		if err != nil {
			return fmt.Errorf("error deleting rows from %T: %w", schema, err)
		}
	}
	log.Debugf("completed deleting session %s", sessionID)

	return nil
}
