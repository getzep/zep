package postgres

import (
	"context"
	"database/sql"
	"fmt"
	"runtime"
	"strings"
	"time"

	"github.com/uptrace/bun/driver/pgdriver"

	"github.com/getzep/zep/pkg/llms"
	"github.com/uptrace/bun/dialect/pgdialect"

	"github.com/getzep/zep/pkg/models"

	"github.com/google/uuid"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

type SessionSchema struct {
	bun.BaseModel `bun:"table:session,alias:s"`

	UUID      uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"`
	SessionID string                 `bun:",unique,notnull"`
	CreatedAt time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp"`
	UpdatedAt time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp"`
	DeletedAt time.Time              `bun:"type:timestamptz,soft_delete,nullzero"`
	Metadata  map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"`
}

// BeforeCreateTable is a marker method to ensure uniform interface across all table models - used in table creation iterator
func (s *SessionSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

type MessageStoreSchema struct {
	bun.BaseModel `bun:"table:message,alias:m"`

	// TODO: replace UUIDs with sortable ULIDs or UUIDv7s to avoid having to have both a UUID and an ID.
	// see https://blog.daveallie.com/ulid-primary-keys
	UUID uuid.UUID `bun:",pk,type:uuid,default:gen_random_uuid()"`
	// ID is used only for sorting / slicing purposes as we can't sort by CreatedAt for messages created simultaneously
	ID         int64                  `bun:",autoincrement"`
	CreatedAt  time.Time              `bun:"type:timestamptz,notnull,default:current_timestamp"`
	UpdatedAt  time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt  time.Time              `bun:"type:timestamptz,soft_delete,nullzero"`
	SessionID  string                 `bun:",notnull"`
	Role       string                 `bun:",notnull"`
	Content    string                 `bun:",notnull"`
	TokenCount int                    `bun:",notnull"`
	Metadata   map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"`
	Session    *SessionSchema         `bun:"rel:belongs-to,join:session_id=session_id,on_delete:cascade"`
}

func (s *MessageStoreSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

// MessageVectorStoreSchema stores the embeddings for a message.
// TODO: Vector dims from config
type MessageVectorStoreSchema struct {
	bun.BaseModel `bun:"table:message_embedding,alias:me"`

	UUID        uuid.UUID           `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt   time.Time           `bun:"type:timestamptz,notnull,default:current_timestamp"`
	UpdatedAt   time.Time           `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt   time.Time           `bun:"type:timestamptz,soft_delete,nullzero"`
	SessionID   string              `bun:",notnull"`
	MessageUUID uuid.UUID           `bun:"type:uuid,notnull,unique"`
	Embedding   pgvector.Vector     `bun:"type:vector(1536)"`
	IsEmbedded  bool                `bun:"type:bool,notnull,default:false"`
	Session     *SessionSchema      `bun:"rel:belongs-to,join:session_id=session_id,on_delete:cascade"`
	Message     *MessageStoreSchema `bun:"rel:belongs-to,join:message_uuid=uuid,on_delete:cascade"`
}

func (s *MessageVectorStoreSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

type SummaryStoreSchema struct {
	bun.BaseModel `bun:"table:summary,alias:su"`

	UUID             uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt        time.Time              `bun:"type:timestamptz,notnull,default:current_timestamp"`
	UpdatedAt        time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt        time.Time              `bun:"type:timestamptz,soft_delete,nullzero"`
	SessionID        string                 `bun:",notnull"`
	Content          string                 `bun:",nullzero"` // allow null as we might want to use Metadata without a summary
	Metadata         map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"`
	TokenCount       int                    `bun:",notnull"`
	SummaryPointUUID uuid.UUID              `bun:"type:uuid,notnull,unique"` // the UUID of the most recent message that was used to create the summary
	Session          *SessionSchema         `bun:"rel:belongs-to,join:session_id=session_id,on_delete:cascade"`
	Message          *MessageStoreSchema    `bun:"rel:belongs-to,join:summary_point_uuid=uuid,on_delete:cascade"`
}

func (s *SummaryStoreSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

// DocumentCollectionSchema represents the schema for the DocumentCollectionDAO table.
type DocumentCollectionSchema struct {
	bun.BaseModel `bun:"table:document_collection,alias:dc"`
	models.DocumentCollection
}

func (s *DocumentCollectionSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

// DocumentSchemaTemplate represents the schema template for Document tables.
// MessageEmbedding is manually added when createDocumentTable is run in order to set the correct dimensions.
// This means the embedding is not returned when querying using bun.
type DocumentSchemaTemplate struct {
	bun.BaseModel `bun:"table:document,alias:d"`
	models.DocumentBase
}

// Create session_id indexes after table creation
var _ bun.AfterCreateTableHook = (*SessionSchema)(nil)
var _ bun.AfterCreateTableHook = (*MessageStoreSchema)(nil)
var _ bun.AfterCreateTableHook = (*MessageVectorStoreSchema)(nil)
var _ bun.AfterCreateTableHook = (*SummaryStoreSchema)(nil)

// Create Collection Name index after table creation
var _ bun.AfterCreateTableHook = (*DocumentCollectionSchema)(nil)

func (*SessionSchema) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	_, err := query.DB().NewCreateIndex().
		Model((*SessionSchema)(nil)).
		Index("session_session_id_idx").
		Column("session_id").
		Exec(ctx)
	return err
}

func (*MessageStoreSchema) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	colsToIndex := []string{"session_id", "id"}
	for _, col := range colsToIndex {
		_, err := query.DB().NewCreateIndex().
			Model((*MessageStoreSchema)(nil)).
			Index(fmt.Sprintf("memstore_%s_idx", col)).
			Column(col).
			Exec(ctx)
		if err != nil {
			return err
		}
	}
	return nil
}

func (*MessageVectorStoreSchema) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	_, err := query.DB().NewCreateIndex().
		Model((*MessageVectorStoreSchema)(nil)).
		Index("mem_vec_store_session_id_idx").
		Column("session_id").
		Exec(ctx)
	return err
}

func (*SummaryStoreSchema) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	_, err := query.DB().NewCreateIndex().
		Model((*SummaryStoreSchema)(nil)).
		Index("sumstore_session_id_idx").
		Column("session_id").
		Exec(ctx)
	return err
}

func (*DocumentCollectionSchema) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	_, err := query.DB().NewCreateIndex().
		Model((*DocumentCollectionSchema)(nil)).
		Index("document_collection_name_idx").
		Column("name").
		Exec(ctx)
	return err
}

var messageTableList = []bun.BeforeCreateTableHook{
	&MessageVectorStoreSchema{},
	&SummaryStoreSchema{},
	&MessageStoreSchema{},
	&SessionSchema{},
}

// generateDocumentTableName generates a table name for a collection.
// If the table already exists, the table is not recreated.
func createDocumentTable(
	ctx context.Context,
	db *bun.DB,
	tableName string,
	embeddingDimensions int,
) error {
	schema := &DocumentSchemaTemplate{}
	_, err := db.NewCreateTable().
		Model(schema).
		// override default table name
		ModelTableExpr("?", bun.Ident(tableName)).
		// create the embedding column using the provided dimensions
		ColumnExpr("embedding vector(?)", embeddingDimensions).
		IfNotExists().
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("error creating document table: %w", err)
	}

	// Create document_id indexe
	_, err = db.NewCreateIndex().
		Model(schema).
		// override default table name
		ModelTableExpr("?", bun.Ident(tableName)).
		Index(tableName + "document_id_idx").
		Column("document_id").
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("error creating session_session_id_idx: %w", err)
	}

	return nil
}

// ensurePostgresSetup creates the db schema if it does not exist.
func ensurePostgresSetup(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
) error {
	_, err := db.Exec("CREATE EXTENSION IF NOT EXISTS vector")
	if err != nil {
		return fmt.Errorf("error creating pgvector extension: %w", err)
	}

	// Create new tableList slice and append DocumentCollectionSchema to it
	tableList := append(messageTableList, &DocumentCollectionSchema{}) //nolint:gocritic
	// iterate through messageTableList in reverse order to create tables with foreign keys first
	for i := len(tableList) - 1; i >= 0; i-- {
		schema := tableList[i]
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

	model, err := llms.GetEmbeddingModel(appState, "message")
	if err != nil {
		return fmt.Errorf("error getting message embedding model: %w", err)
	}
	// we keep this at 1536 for legacy reasons, despite the default now being 384
	if model.Dimensions != 1536 {
		err := migrateMessageEmbeddingDims(ctx, db, model.Dimensions)
		if err != nil {
			return fmt.Errorf("error migrating message embedding dimensions: %w", err)
		}
	}

	return nil
}

// migrateMessageEmbeddingDims drops the old embedding column and creates a new one with the
// correct dimensions.
func migrateMessageEmbeddingDims(
	ctx context.Context,
	db *bun.DB,
	dimensions int,
) error {
	columnQuery := `DO $$ 
BEGIN 
    IF EXISTS (
        SELECT 1 
        FROM   information_schema.columns 
        WHERE  table_name = 'message_embedding' 
        AND    column_name = 'embedding'
    ) THEN 
        ALTER TABLE message_embedding DROP COLUMN embedding; 
    END IF; 
END $$;`

	_, err := db.ExecContext(ctx, columnQuery)
	if err != nil {
		return fmt.Errorf("error dropping column embedding: %w", err)
	}
	_, err = db.NewAddColumn().
		Model((*MessageVectorStoreSchema)(nil)).
		ColumnExpr(fmt.Sprintf("embedding vector(%d)", dimensions)).
		Exec(ctx)
	if err != nil {
		return fmt.Errorf("error adding column MessageEmbedding: %w", err)
	}

	return nil
}

// NewPostgresConn creates a new bun.DB connection to a postgres database using the provided DSN.
// The connection is configured to pool connections based on the number of PROCs available.
func NewPostgresConn(appState *models.AppState) *bun.DB {
	maxOpenConns := 4 * runtime.GOMAXPROCS(0)

	sqldb := sql.OpenDB(
		pgdriver.NewConnector(pgdriver.WithDSN(appState.Config.Store.Postgres.DSN)),
	)
	sqldb.SetMaxOpenConns(maxOpenConns)
	sqldb.SetMaxIdleConns(maxOpenConns)

	db := bun.NewDB(sqldb, pgdialect.New())
	return db
}
