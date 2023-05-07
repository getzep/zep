package memorystore

import (
	"context"
	"database/sql"
	"fmt"
	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/models"
	"github.com/google/uuid"
	"github.com/jinzhu/copier"
	"github.com/pgvector/pgvector-go"
	"github.com/spf13/viper"
	"github.com/uptrace/bun"
	"github.com/uptrace/bun/dialect/pgdialect"
	"github.com/uptrace/bun/driver/pgdriver"
	"runtime"
	"strings"
	"time"
)

// NewPostgresMemoryStore returns a new PostgresMemoryStore. Use this to correctly initialize the store.
func NewPostgresMemoryStore(
	appState *models.AppState,
	client *bun.DB,
) (*PostgresMemoryStore, error) {
	pms := &PostgresMemoryStore{models.BaseMemoryStore[*bun.DB]{Client: client}}
	err := pms.OnStart(context.Background(), appState)
	if err != nil {
		return nil, NewStorageError("failed to run OnInit", err)
	}
	return pms, nil
}

type PgSession struct {
	bun.BaseModel `bun:"table:session,alias:s"`

	UUID      uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"`
	SessionID string                 `bun:",unique,notnull"`
	CreatedAt time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp"`
	UpdatedAt time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp"`
	DeletedAt time.Time              `bun:"type:timestamptz,soft_delete,nullzero"`
	Metadata  map[string]interface{} `bun:"type:jsonb,nullzero"`
}

// dummy method to ensure uniform interface across all table models - used in table creation iterator
func (s *PgSession) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

type PgMessageStore struct {
	bun.BaseModel `bun:"table:message,alias:m"`

	// TODO: replace UUIDs with sortable ULIDs or UUIDv7s to avoid having to have both a UUID and an ID.
	// see https://blog.daveallie.com/ulid-primary-keys
	UUID uuid.UUID `bun:",pk,type:uuid,default:gen_random_uuid()"`
	// ID is used only for sorting / slicing purposes as we can't sort by CreatedAt for messages created simultaneously
	ID        int64                  `bun:",autoincrement"`
	CreatedAt time.Time              `bun:"type:timestamptz,notnull,default:current_timestamp"`
	UpdatedAt time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt time.Time              `bun:"type:timestamptz,soft_delete,nullzero"`
	SessionID string                 `bun:",notnull"`
	Role      string                 `bun:",notnull"`
	Content   string                 `bun:",notnull"`
	Metadata  map[string]interface{} `bun:"type:jsonb,nullzero"`
	Session   *PgSession             `bun:"rel:belongs-to,join:session_id=session_id,on_delete:cascade"`
}

func (s *PgMessageStore) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

// PgMessageVectorStore stores the embeddings for a message.
// TODO: Vector dims from config
type PgMessageVectorStore struct {
	bun.BaseModel `bun:"table:message_embedding,alias:me"`

	UUID        uuid.UUID       `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt   time.Time       `bun:"type:timestamptz,notnull,default:current_timestamp"`
	UpdatedAt   time.Time       `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt   time.Time       `bun:"type:timestamptz,soft_delete,nullzero"`
	SessionID   string          `bun:",notnull"`
	MessageUUID uuid.UUID       `bun:"type:uuid,notnull,unique"`
	Embedding   pgvector.Vector `bun:"type:vector(1536)"`
	IsEmbedded  bool            `bun:"type:bool,notnull,default:false"`
	Session     *PgSession      `bun:"rel:belongs-to,join:session_id=session_id,on_delete:cascade"`
	Message     *PgMessageStore `bun:"rel:belongs-to,join:message_uuid=uuid,on_delete:cascade"`
}

func (s *PgMessageVectorStore) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

type PgSummaryStore struct {
	bun.BaseModel `bun:"table:summary,alias:su"`

	UUID             uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt        time.Time              `bun:"type:timestamptz,notnull,default:current_timestamp"`
	UpdatedAt        time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt        time.Time              `bun:"type:timestamptz,soft_delete,nullzero"`
	SessionID        string                 `bun:",notnull"`
	Content          string                 `bun:",nullzero"` // allow null as we might want to use Metadata without a summary
	Metadata         map[string]interface{} `bun:"type:jsonb,nullzero"`
	TokenCount       int                    `bun:",notnull"`
	SummaryPointUUID uuid.UUID              `bun:"type:uuid,notnull,unique"` // the UUID of the most recent message that was used to create the summary
	Session          *PgSession             `bun:"rel:belongs-to,join:session_id=session_id,on_delete:cascade"`
	Message          *PgMessageStore        `bun:"rel:belongs-to,join:summary_point_uuid=uuid,on_delete:cascade"`
}

func (s *PgSummaryStore) BeforeCreateTable(
	_ context.Context,
	query *bun.CreateTableQuery,
) error {
	return nil
}

// Create session_id indexes after table creation
var _ bun.AfterCreateTableHook = (*PgSession)(nil)
var _ bun.AfterCreateTableHook = (*PgMessageStore)(nil)
var _ bun.AfterCreateTableHook = (*PgMessageVectorStore)(nil)
var _ bun.AfterCreateTableHook = (*PgSummaryStore)(nil)

func (*PgSession) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	_, err := query.DB().NewCreateIndex().
		Model((*PgSession)(nil)).
		Index("session_session_id_idx").
		Column("session_id").
		Exec(ctx)
	return err
}

func (*PgMessageStore) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	colsToIndex := []string{"session_id", "id"}
	for _, col := range colsToIndex {
		_, err := query.DB().NewCreateIndex().
			Model((*PgMessageStore)(nil)).
			Index(fmt.Sprintf("memstore_%s_idx", col)).
			Column(col).
			Exec(ctx)
		if err != nil {
			return err
		}
	}
	return nil
}

func (*PgMessageVectorStore) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	_, err := query.DB().NewCreateIndex().
		Model((*PgMessageVectorStore)(nil)).
		Index("mem_vec_store_session_id_idx").
		Column("session_id").
		Exec(ctx)
	return err
}

func (*PgSummaryStore) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	_, err := query.DB().NewCreateIndex().
		Model((*PgSummaryStore)(nil)).
		Index("sumstore_session_id_idx").
		Column("session_id").
		Exec(ctx)
	return err
}

// Force compiler to validate that PostgresMemoryStore implements the MemoryStore interface.
var _ models.MemoryStore[*bun.DB] = &PostgresMemoryStore{}

type PostgresMemoryStore struct {
	models.BaseMemoryStore[*bun.DB]
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

// GetMemory returns the memory for a given sessionID.
func (pms *PostgresMemoryStore) GetMemory(
	ctx context.Context,
	_ *models.AppState,
	sessionID string,
	lastNMessages int,
) (*models.Memory, error) {
	err := checkLastNParms(0, lastNMessages)
	if err != nil {
		return nil, NewStorageError("invalid lastNMessages or lastNTokens in get call", err)
	}

	// Get the most recent summary
	summary, err := getSummary(ctx, pms.Client, sessionID)
	if err != nil {
		return nil, NewStorageError("failed to get summary", err)
	}

	// Retrieve either the lastNMessages or all messages up to the last SummaryPoint
	messages, err := getMessages(ctx, pms.Client, sessionID, lastNMessages)
	if err != nil {
		return nil, NewStorageError("failed to get messages", err)
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
	_ *models.AppState,
	sessionID string,
	memoryMessages *models.Memory,
) error {
	_, err := putMessages(ctx, pms.Client, sessionID, memoryMessages.Messages)
	if err != nil {
		return NewStorageError("failed to put messages", err)
	}
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

func searchMessages(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	sessionID string,
	query *models.SearchPayload,
	limit int,
) ([]models.SearchResult, error) {
	s := query.Text
	if s == "" {
		return nil, NewStorageError("empty query", fmt.Errorf("empty query"))
	}

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
		// use dotproduct for OpenAI embeddings as they're normalized to 1
		// https://platform.openai.com/docs/guides/embeddings/which-distance-function-should-i-use
		// multiply by -1 as pgvector returns the negative inner product
		ColumnExpr("(embedding <#> ? ) * -1 AS dist", vector).
		Where("m.session_id = ?", sessionID).
		Order("dist DESC").
		Limit(limit).
		Scan(ctx, &results)
	if err != nil {
		return nil, NewStorageError("memory searchMessages failed", err)
	}
	return results, nil
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
	isEmbedded bool,
) error {
	if embeddings == nil {
		return NewStorageError("nil embeddings received", nil)
	}
	if len(embeddings) == 0 {
		return NewStorageError("no embeddings received", nil)
	}

	err := putEmbeddings(ctx, pms.Client, sessionID, embeddings, isEmbedded)
	if err != nil {
		return NewStorageError("failed to put embeddings", err)
	}

	return nil
}

func (pms *PostgresMemoryStore) GetMessageVectors(ctx context.Context,
	_ *models.AppState,
	sessionID string,
	isEmbedded bool,
) ([]models.Embeddings, error) {
	embeddings, err := getMessageVectors(ctx, pms.Client, sessionID, isEmbedded)
	if err != nil {
		return nil, NewStorageError("GetMessageVectors failed to get embeddings", err)
	}

	return embeddings, nil
}

func getMessageVectors(ctx context.Context,
	db *bun.DB,
	sessionID string,
	isEmbedded bool) ([]models.Embeddings, error) {
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
		Where("message_embedding.is_embedded = ?", isEmbedded).
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

// putMessages stores a new messages for a session.
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

	// Create or update a Session
	_, err := putSession(ctx, db, sessionID, nil)
	if err != nil {
		return nil, NewStorageError("failed to put session", err)
	}

	pgMessages := make([]PgMessageStore, len(messages))
	err = copier.CopyWithOption(
		&pgMessages,
		&messages,
		copier.Option{IgnoreEmpty: true, DeepCopy: true}, // TODO: check if this is needed
	)
	if err != nil {
		return nil, NewStorageError("failed to copy messages to pgMessages", err)
	}

	for i := range pgMessages {
		pgMessages[i].SessionID = sessionID
	}

	// wrap in a transaction so we can rollback if any of the inserts fail. We don't want to
	// partially save messages without vectorstore records.
	tx, err := db.Begin()
	if err != nil {
		return nil, err
	}
	defer func(tx bun.Tx) {
		err := tx.Rollback()
		if err != nil {
			log.Error("failed to rollback transaction", err)
		}
	}(tx)

	_, err = tx.NewInsert().Model(&pgMessages).Exec(ctx)
	if err != nil {
		return nil, NewStorageError("failed to save memories to store", err)
	}

	// If embeddings are enabled, store the new messages for future embedding.
	// The Embedded field will be false until we run the embedding extractor out of band.
	if viper.GetBool("extractor.embeddings.enabled") {
		zeroVector := make([]float32, 1536) // TODO: use config
		embedRecords := make([]PgMessageVectorStore, len(messages))
		for i, msg := range pgMessages {
			embedRecords[i] = PgMessageVectorStore{
				SessionID:   sessionID,
				MessageUUID: msg.UUID,
				Embedding:   pgvector.NewVector(zeroVector), // Vector fields can't be null
			}
		}
		_, err = tx.NewInsert().Model(&embedRecords).On("CONFLICT DO NOTHING").Exec(ctx)
		if err != nil {
			return nil, NewStorageError("failed to save memory vector records", err)
		}
	}

	if err := tx.Commit(); err != nil {
		return nil, err
	}

	retMessages := make([]models.Message, len(messages))
	err = copier.Copy(&retMessages, &pgMessages)
	if err != nil {
		return nil, NewStorageError("failed to copy pgMessages to retMessages", err)
	}

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

// getSummary returns the summary for a session, limited by either lastNMessages or to the last SummaryPoint.
func getMessages(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	lastNMessages int,
) ([]models.Message, error) {
	if sessionID == "" {
		return nil, NewStorageError("sessionID cannot be empty", nil)
	}

	configuredMessageWindow := viper.GetInt("memory.message_window")
	if configuredMessageWindow == 0 {
		return nil, NewStorageError("memory.message_window must be greater than 0", nil)
	}

	// if lastNMessages is 0, get the last SummaryPoint. If there is no summary, return the configured message window
	var summary *models.Summary
	var err error
	if lastNMessages == 0 {
		summary, err = getSummary(ctx, db, sessionID)
		if err != nil {
			return nil, NewStorageError("unable to retrieve summary", nil)
		}

		// if no summary has been created yet, set lastNMessages to the configured message window
		if summary == nil {
			lastNMessages = configuredMessageWindow
		}
	}

	// if we do have a summary, determine the date of the last message in the summary
	var summaryPointIndex int64 = 0
	if summary != nil {
		summaryPointIndex, err = lastSummaryPointIndex(
			ctx,
			db,
			sessionID,
			summary.SummaryPointUUID,
			configuredMessageWindow,
		)
		if err != nil {
			return nil, NewStorageError("unable to retrieve last summary point", err)
		}
	}

	messages := []PgMessageStore{}
	query := db.NewSelect().
		Model(&messages).
		Where("session_id = ?", sessionID).
		Order("id DESC") // Return messages in reverse chronological order (using

	if lastNMessages > 0 {
		query.Limit(lastNMessages)
	}

	// Only get messages created after the SummaryPoint
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

// lastSummaryPointCreatedAt returns the ID of the last SummaryPoint
// message. We use the configured message window to determine the number of
// messages to retrieve from the db as this is guaranteed to be greater than the
// number of messages between the SummaryPoint and the most recent message in the
// session.
func lastSummaryPointIndex(
	ctx context.Context,
	db *bun.DB,
	sessionID string,
	summaryPointUUID uuid.UUID,
	configuredMessageWindow int,
) (int64, error) {
	var messages []*PgMessageStore
	err := db.NewSelect().
		Model(&messages).
		Column("uuid", "id").
		Where("session_id = ?", sessionID).
		Order("id DESC").
		Limit(configuredMessageWindow).
		Scan(ctx)

	if err != nil {
		return 0, NewStorageError(
			"failed to get messages when determining SummaryPoint ID",
			err,
		)
	}

	for _, message := range messages {
		if message.UUID == summaryPointUUID {
			return message.ID, nil
		}
	}

	return 0, NewStorageError(
		fmt.Sprintf("message with UUID %s not found", summaryPointUUID),
		nil,
	)
}

// getSummary returns the most recent summary for a session
func getSummary(ctx context.Context, db *bun.DB, sessionID string) (*models.Summary, error) {
	summary := PgSummaryStore{}
	err := db.NewSelect().
		Model(&summary).
		Where("session_id = ?", sessionID).
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
	isEmbedded bool,
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
		}
	}

	values := db.NewValues(&embeddingVectors)
	_, err := db.NewUpdate().
		With("_data", values).
		Model((*PgMessageVectorStore)(nil)).
		TableExpr("_data").
		Set("embedding = _data.embedding").
		Set("is_embedded = ?", isEmbedded).
		Where("me.message_uuid = _data.message_uuid").
		OmitZero().
		Exec(ctx)
	if err != nil {
		return NewStorageError("failed to insert message vectors", err)
	}

	return nil
}

// deleteSession deletes a session from the memory store. This is a soft delete.
// TODO: This is ugly. Determine why bun's cascading deletes aren't working
func deleteSession(ctx context.Context, db *bun.DB, sessionID string) error {
	schemas := []bun.BeforeCreateTableHook{
		&PgMessageVectorStore{},
		&PgSummaryStore{},
		&PgMessageStore{},
		&PgSession{},
	}
	for _, schema := range schemas {
		_, err := db.NewDelete().
			Model(schema).
			Where("session_id = ?", sessionID).
			Exec(ctx)
		if err != nil {
			return fmt.Errorf("error deleting rows from %T: %w", schema, err)
		}
	}

	return nil
}

// ensurePostgresSetup creates the db schema if it does not exist.
func ensurePostgresSetup(
	ctx context.Context,
	db *bun.DB,
) error {
	_, err := db.Exec("CREATE EXTENSION IF NOT EXISTS vector")
	if err != nil {
		return fmt.Errorf("error creating pgvector extension: %w", err)
	}

	schemas := []bun.BeforeCreateTableHook{
		&PgSession{},
		&PgMessageStore{},
		&PgMessageVectorStore{},
		&PgSummaryStore{},
	}
	for _, schema := range schemas {
		_, err := db.NewCreateTable().
			Model(schema).
			IfNotExists().
			WithForeignKeys().
			Exec(ctx)
		if err != nil {
			// bun still trying to create indexes despite IfNotExists flag
			if strings.Contains(err.Error(), "already exists") {
				continue
			}
			return fmt.Errorf("error creating table for schema %T: %w", schema, err)
		}
	}

	return nil
}

// NewPostgresConn creates a new bun.DB connection to a postgres database using the provided DSN.
// The connection is configured to pool connections based on the number of PROCs available.
func NewPostgresConn(dsn string) *bun.DB {
	maxOpenConns := 4 * runtime.GOMAXPROCS(0)
	if dsn == "" {
		log.Fatal("dsn may not be empty")
	}
	sqldb := sql.OpenDB(pgdriver.NewConnector(pgdriver.WithDSN(dsn)))
	sqldb.SetMaxOpenConns(maxOpenConns)
	sqldb.SetMaxIdleConns(maxOpenConns)
	db := bun.NewDB(sqldb, pgdialect.New())
	return db
}
