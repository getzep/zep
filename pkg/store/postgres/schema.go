package postgres

import (
	"context"
	"database/sql"
	"fmt"
	"runtime"
	"strings"
	"time"

	"github.com/Masterminds/semver/v3"
	"github.com/uptrace/bun/driver/pgdriver"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/store/postgres/migrations"
	"github.com/uptrace/bun/dialect/pgdialect"

	"github.com/getzep/zep/pkg/models"

	"github.com/google/uuid"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

type SessionSchema struct {
	bun.BaseModel `bun:"table:session,alias:s" yaml:"-"`

	UUID      uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"                     yaml:"uuid,omitempty"`
	ID        int64                  `bun:",autoincrement"                                              yaml:"id,omitempty"` // used as a cursor for pagination
	SessionID string                 `bun:",unique,notnull"                                             yaml:"session_id,omitempty"`
	CreatedAt time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp" yaml:"created_at,omitempty"`
	UpdatedAt time.Time              `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp" yaml:"updated_at,omitempty"`
	DeletedAt time.Time              `bun:"type:timestamptz,soft_delete,nullzero"                       yaml:"deleted_at,omitempty"`
	Metadata  map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"                         yaml:"metadata,omitempty"`
	// UserUUID must be pointer type in order to be nullable
	UserID *string     `bun:","                                                           yaml:"user_id,omitempty"`
	User   *UserSchema `bun:"rel:belongs-to,join:user_id=user_id,on_delete:cascade"       yaml:"-"`
}

var _ bun.BeforeAppendModelHook = (*SessionSchema)(nil)

func (s *SessionSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		s.UpdatedAt = time.Now()
	}
	return nil
}

// BeforeCreateTable is a marker method to ensure uniform interface across all table models - used in table creation iterator
func (s *SessionSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

type MessageStoreSchema struct {
	bun.BaseModel `bun:"table:message,alias:m" yaml:"-"`

	UUID uuid.UUID `bun:",pk,type:uuid,default:gen_random_uuid()"                     yaml:"uuid"`
	// ID is used only for sorting / slicing purposes as we can't sort by CreatedAt for messages created simultaneously
	ID         int64                  `bun:",autoincrement"                                              yaml:"id,omitempty"`
	CreatedAt  time.Time              `bun:"type:timestamptz,notnull,default:current_timestamp"          yaml:"created_at,omitempty"`
	UpdatedAt  time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp"         yaml:"updated_at,omitempty"`
	DeletedAt  time.Time              `bun:"type:timestamptz,soft_delete,nullzero"                       yaml:"deleted_at,omitempty"`
	SessionID  string                 `bun:",notnull"                                                    yaml:"session_id,omitempty"`
	Role       string                 `bun:",notnull"                                                    yaml:"role,omitempty"`
	Content    string                 `bun:",notnull"                                                    yaml:"content,omitempty"`
	TokenCount int                    `bun:",notnull"                                                    yaml:"token_count,omitempty"`
	Metadata   map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"                         yaml:"metadata,omitempty"`
	Session    *SessionSchema         `bun:"rel:belongs-to,join:session_id=session_id,on_delete:cascade" yaml:"-"`
}

var _ bun.BeforeAppendModelHook = (*MessageStoreSchema)(nil)

func (s *MessageStoreSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		s.UpdatedAt = time.Now()
	}
	return nil
}

func (s *MessageStoreSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

// MessageVectorStoreSchema stores the embeddings for a message.
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

var _ bun.BeforeAppendModelHook = (*MessageVectorStoreSchema)(nil)

func (s *MessageVectorStoreSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		s.UpdatedAt = time.Now()
	}
	return nil
}

func (s *MessageVectorStoreSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

type SummaryStoreSchema struct {
	bun.BaseModel `bun:"table:summary,alias:su" ,yaml:"-"`

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

var _ bun.BeforeAppendModelHook = (*SummaryStoreSchema)(nil)

func (s *SummaryStoreSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		s.UpdatedAt = time.Now()
	}
	return nil
}

func (s *SummaryStoreSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

// DocumentCollectionSchema represents the schema for the DocumentCollectionDAO table.
type DocumentCollectionSchema struct {
	bun.BaseModel             `bun:"table:document_collection,alias:dc" yaml:"-"`
	models.DocumentCollection `                                         yaml:",inline"`
}

func (s *DocumentCollectionSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

var _ bun.BeforeAppendModelHook = (*DocumentCollectionSchema)(nil)

func (s *DocumentCollectionSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		s.UpdatedAt = time.Now()
	}
	return nil
}

// DocumentSchemaTemplate represents the schema template for Document tables.
// MessageEmbedding is manually added when createDocumentTable is run in order to set the correct dimensions.
// This means the embedding is not returned when querying using bun.
type DocumentSchemaTemplate struct {
	bun.BaseModel `bun:"table:document,alias:d"`
	models.DocumentBase
}

type UserSchema struct {
	bun.BaseModel `bun:"table:users,alias:u" yaml:"-"`

	UUID      uuid.UUID              `bun:",pk,type:uuid,default:gen_random_uuid()"             yaml:"uuid,omitempty"`
	ID        int64                  `bun:",autoincrement"                                      yaml:"id,omitempty"` // used as a cursor for pagination
	CreatedAt time.Time              `bun:"type:timestamptz,notnull,default:current_timestamp"  yaml:"created_at,omitempty"`
	UpdatedAt time.Time              `bun:"type:timestamptz,nullzero,default:current_timestamp" yaml:"updated_at,omitempty"`
	DeletedAt time.Time              `bun:"type:timestamptz,soft_delete,nullzero"               yaml:"deleted_at,omitempty"`
	UserID    string                 `bun:",unique,notnull"                                     yaml:"user_id,omitempty"`
	Email     string                 `bun:","                                                   yaml:"email,omitempty"`
	FirstName string                 `bun:","                                                   yaml:"first_name,omitempty"`
	LastName  string                 `bun:","                                                   yaml:"last_name,omitempty"`
	Metadata  map[string]interface{} `bun:"type:jsonb,nullzero,json_use_number"                 yaml:"metadata,omitempty"`
}

var _ bun.BeforeAppendModelHook = (*UserSchema)(nil)

func (u *UserSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		u.UpdatedAt = time.Now()
	}
	return nil
}

// BeforeCreateTable is a marker method to ensure uniform interface across all table models - used in table creation iterator
func (u *UserSchema) BeforeCreateTable(
	_ context.Context,
	_ *bun.CreateTableQuery,
) error {
	return nil
}

// Create session_id indexes after table creation
var _ bun.AfterCreateTableHook = (*SessionSchema)(nil)
var _ bun.AfterCreateTableHook = (*MessageStoreSchema)(nil)
var _ bun.AfterCreateTableHook = (*MessageVectorStoreSchema)(nil)
var _ bun.AfterCreateTableHook = (*SummaryStoreSchema)(nil)
var _ bun.AfterCreateTableHook = (*UserSchema)(nil)

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
		IfNotExists().
		Exec(ctx)
	if err != nil {
		return err
	}

	_, err = query.DB().NewCreateIndex().
		Model((*SessionSchema)(nil)).
		Index("session_user_id_idx").
		Column("user_id").
		IfNotExists().
		Exec(ctx)
	if err != nil {
		return err
	}

	return nil
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
			IfNotExists().
			Column(col).
			IfNotExists().
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
		IfNotExists().
		Column("session_id").
		IfNotExists().
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
		IfNotExists().
		Column("session_id").
		IfNotExists().
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
		IfNotExists().
		Column("name").
		IfNotExists().
		Exec(ctx)
	return err
}

func (*UserSchema) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	_, err := query.DB().NewCreateIndex().
		Model((*UserSchema)(nil)).
		Index("user_user_id_idx").
		Column("user_id").
		IfNotExists().
		Exec(ctx)
	if err != nil {
		return err
	}

	_, err = query.DB().NewCreateIndex().
		Model((*UserSchema)(nil)).
		Index("user_email_idx").
		Column("email").
		IfNotExists().
		Exec(ctx)
	if err != nil {
		return err
	}

	return nil
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
	appState *models.AppState,
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

	// Create document_id index
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

	// If HNSW indexes are available, create an HNSW index on the embedding column
	if appState.Config.Store.Postgres.AvailableIndexes.HSNW {
		err = createHNSWIndex(ctx, db, tableName, "embedding")
		if err != nil {
			return fmt.Errorf("error creating hnsw index: %w", err)
		}
	}

	return nil
}

// enablePgVectorExtension creates the pgvector extension if it does not exist and updates it if it is out of date.
func enablePgVectorExtension(ctx context.Context, db *bun.DB) error {
	// Create pgvector extension if it does not exist
	_, err := db.Exec("CREATE EXTENSION IF NOT EXISTS vector")
	if err != nil {
		return fmt.Errorf("error creating pgvector extension: %w", err)
	}

	// if this is an upgrade, we may need to update the pgvector extension
	// this is a no-op if the extension is already up to date
	_, err = db.Exec("ALTER EXTENSION vector UPDATE")
	if err != nil {
		return fmt.Errorf("error updating pgvector extension: %w", err)
	}

	return nil
}

// CreateSchema creates the db schema if it does not exist.
func CreateSchema(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
) error {
	// Create new tableList slice and append DocumentCollectionSchema to it
	tableList := append( //nolint:gocritic
		messageTableList,
		&UserSchema{},
		&DocumentCollectionSchema{},
	)
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

	// check that the message embedding dimensions match the configured model
	if err := checkMessageEmbeddingDims(ctx, appState, db); err != nil {
		return fmt.Errorf("error checking message embedding dimensions: %w", err)
	}

	// Create HNSW index on messages_embeddings if available
	t := "message_embedding"
	c := "embedding"
	if appState.Config.Store.Postgres.AvailableIndexes.HSNW {
		if err := createHNSWIndex(ctx, db, t, c); err != nil {
			return fmt.Errorf("error creating hnsw index: %w", err)
		}
	}

	// apply migrations
	if err := migrations.Migrate(ctx, db); err != nil {
		return fmt.Errorf("failed to apply migrations: %w", err)
	}

	return nil
}

// createHNSWIndex creates an HNSW index on the given table and column if it does not exist.
// The index is created with the default M and efConstruction values. Only vector_cosine_ops is supported.
func createHNSWIndex(ctx context.Context, db *bun.DB, table, column string) error {
	const (
		m              = 16
		efConstruction = 64
	)

	idx := table + "_" + column + "_hnsw_idx"

	log.Infof("creating hnsw index on %s.%s if it does not exist", table, column)

	_, err := db.ExecContext(
		ctx,
		"CREATE INDEX CONCURRENTLY IF NOT EXISTS ? ON ? USING hnsw (? vector_cosine_ops) WITH (M = ?, ef_construction = ?);",
		bun.Safe(idx),
		bun.Ident(table),
		bun.Ident(column),
		m,
		efConstruction,
	)
	if err != nil {
		return err
	}

	log.Infof("created hnsw index successfully on %s.%s if it did not exist", table, column)

	return nil
}

// checkMessageEmbeddingDims checks the dimensions of the message embedding column against the
// dimensions of the configured message embedding model. If they do not match, the column is dropped and
// recreated with the correct dimensions.
func checkMessageEmbeddingDims(ctx context.Context, appState *models.AppState, db *bun.DB) error {
	model, err := llms.GetEmbeddingModel(appState, "message")
	if err != nil {
		return fmt.Errorf("error getting message embedding model: %w", err)
	}
	width, err := getEmbeddingColumnWidth(ctx, "message_embedding", db)
	if err != nil {
		return fmt.Errorf("error getting embedding column width: %w", err)
	}

	if width != model.Dimensions {
		log.Warnf(
			"message embedding dimensions are %d, expected %d.\n migrating message embedding column width to %d. this may result in loss of existing embedding vectors",
			width,
			model.Dimensions,
			model.Dimensions,
		)
		err := MigrateMessageEmbeddingDims(ctx, db, model.Dimensions)
		if err != nil {
			return fmt.Errorf("error migrating message embedding dimensions: %w", err)
		}
	}
	return nil
}

// getEmbeddingColumnWidth returns the width of the embedding column in the provided table.
func getEmbeddingColumnWidth(ctx context.Context, tableName string, db *bun.DB) (int, error) {
	var width int
	err := db.NewSelect().
		Table("pg_attribute").
		ColumnExpr("atttypmod"). // vector width is stored in atttypmod
		Where("attrelid = ?::regclass", tableName).
		Where("attname = 'embedding'").
		Scan(ctx, &width)
	if err != nil {
		return 0, fmt.Errorf("error getting embedding column width: %w", err)
	}
	return width, nil
}

// MigrateMessageEmbeddingDims drops the old embedding column and creates a new one with the
// correct dimensions.
func MigrateMessageEmbeddingDims(
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
func NewPostgresConn(appState *models.AppState) (*bun.DB, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	maxOpenConns := 4 * runtime.GOMAXPROCS(0)

	// WithReadTimeout is 10 minutes to avoid timeouts when creating indexes.
	// TODO: This is not ideal. Use separate connections for index creation?
	sqldb := sql.OpenDB(
		pgdriver.NewConnector(
			pgdriver.WithDSN(appState.Config.Store.Postgres.DSN),
			pgdriver.WithReadTimeout(10*time.Minute),
		),
	)
	sqldb.SetMaxOpenConns(maxOpenConns)
	sqldb.SetMaxIdleConns(maxOpenConns)

	db := bun.NewDB(sqldb, pgdialect.New())

	// Enable pgvector extension
	err := enablePgVectorExtension(ctx, db)
	if err != nil {
		log.Print("error enabling pgvector extension: ", err)
		return nil, err
	}

	// IVFFLAT indexes are always available
	appState.Config.Store.Postgres.AvailableIndexes.IVFFLAT = true

	// Check if HNSW indexes are available
	isHNSW, err := isHNSWAvailable(ctx, db)
	if err != nil {
		log.Print("error checking if hnsw indexes are available: ", err)
		return nil, err
	}
	if isHNSW {
		appState.Config.Store.Postgres.AvailableIndexes.HSNW = true
	}

	return db, nil
}

// isHNSWAvailable checks if the vector extension version is 0.5.0+.
func isHNSWAvailable(ctx context.Context, db *bun.DB) (bool, error) {
	const minVersion = "0.5.0"
	requiredVersion, err := semver.NewVersion(minVersion)
	if err != nil {
		return false, fmt.Errorf("error parsing required vector extension version: %w", err)
	}

	var version string
	err = db.NewSelect().
		Column("extversion").
		TableExpr("pg_extension").
		Where("extname = 'vector'").
		Scan(ctx, &version)
	if err != nil {
		if err == sql.ErrNoRows {
			// The vector extension is not installed
			log.Debug("vector extension not installed")
			return false, nil
		}
		// An error occurred while executing the query
		return false, fmt.Errorf("error checking vector extension version: %w", err)
	}

	thisVersion, err := semver.NewVersion(version)
	if err != nil {
		return false, fmt.Errorf("error parsing vector extension version: %w", err)
	}

	// Compare the version numbers
	if requiredVersion.GreaterThan(thisVersion) {
		// The vector extension version is < 0.5.0
		log.Infof("vector extension version is < %s. hnsw indexing not available", minVersion)
		return false, nil
	}

	// The vector extension version is >= 0.5.0
	log.Infof("vector extension version is >= %s. hnsw indexing available", minVersion)

	return true, nil
}

type IndexStatus struct {
	Phase       string `bun:"phase"`
	TuplesTotal int    `bun:"tuples_total"`
	TuplesDone  int    `bun:"tuples_done"`
}

// GetIndexStatus queries for an index's status given an index name.
func GetIndexStatus(ctx context.Context, db *bun.DB, indexName string) (IndexStatus, error) {
	var status IndexStatus
	err := db.NewSelect().
		ColumnExpr("i.phase, i.tuples_total, i.tuples_done").
		TableExpr("pg_stat_progress_create_index AS i").
		Join("pg_class AS c ON c.oid = i.index_relid").
		Where("c.relname = ?", indexName).
		Scan(ctx, &status)
	if err != nil {
		return IndexStatus{}, fmt.Errorf("error querying index status: %w", err)
	}

	return status, nil
}
