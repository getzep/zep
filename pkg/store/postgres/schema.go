package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"runtime"
	"strings"
	"time"

	"github.com/uptrace/bun/extra/bunotel"

	"github.com/getzep/zep/pkg/store/postgres/migrations"

	"github.com/Masterminds/semver/v3"
	_ "github.com/jackc/pgx/v5/stdlib" // required for pgx to work
	"github.com/uptrace/bun/driver/pgdriver"

	"github.com/getzep/zep/pkg/llms"
	"github.com/uptrace/bun/dialect/pgdialect"

	"github.com/getzep/zep/pkg/models"

	"github.com/google/uuid"
	"github.com/pgvector/pgvector-go"
	"github.com/uptrace/bun"
)

const defaultEmbeddingDims = 1536

var maxOpenConns = 4 * runtime.GOMAXPROCS(0)

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

type SummaryVectorStoreSchema struct {
	bun.BaseModel `bun:"table:summary_embedding,alias:se" yaml:"-"`

	UUID        uuid.UUID           `bun:",pk,type:uuid,default:gen_random_uuid()"`
	CreatedAt   time.Time           `bun:"type:timestamptz,notnull,default:current_timestamp"`
	UpdatedAt   time.Time           `bun:"type:timestamptz,nullzero,default:current_timestamp"`
	DeletedAt   time.Time           `bun:"type:timestamptz,soft_delete,nullzero"`
	SessionID   string              `bun:",notnull"`
	SummaryUUID uuid.UUID           `bun:"type:uuid,notnull,unique"`
	Embedding   pgvector.Vector     `bun:"type:vector(1536)"`
	IsEmbedded  bool                `bun:"type:bool,notnull,default:false"`
	Summary     *SummaryStoreSchema `bun:"rel:belongs-to,join:summary_uuid=uuid,on_delete:cascade"`
	Session     *SessionSchema      `bun:"rel:belongs-to,join:session_id=session_id,on_delete:cascade"`
}

var _ bun.BeforeAppendModelHook = (*SummaryVectorStoreSchema)(nil)

func (s *SummaryVectorStoreSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		s.UpdatedAt = time.Now()
	}
	return nil
}

// DocumentCollectionSchema represents the schema for the DocumentCollectionDAO table.
type DocumentCollectionSchema struct {
	bun.BaseModel             `bun:"table:document_collection,alias:dc" yaml:"-"`
	models.DocumentCollection `                                         yaml:",inline"`
}

var _ bun.BeforeAppendModelHook = (*DocumentCollectionSchema)(nil)

func (s *DocumentCollectionSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		s.UpdatedAt = time.Now()
	}
	return nil
}

// DocumentSchemaTemplate represents the schema template for Document tables.
// TextData is manually added when createDocumentTable is run in order to set the correct dimensions.
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

// Create session_id indexes after table creation
var _ bun.AfterCreateTableHook = (*SessionSchema)(nil)
var _ bun.AfterCreateTableHook = (*MessageStoreSchema)(nil)
var _ bun.AfterCreateTableHook = (*MessageVectorStoreSchema)(nil)
var _ bun.AfterCreateTableHook = (*SummaryStoreSchema)(nil)
var _ bun.AfterCreateTableHook = (*SummaryVectorStoreSchema)(nil)
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

func (*SummaryVectorStoreSchema) AfterCreateTable(
	ctx context.Context,
	query *bun.CreateTableQuery,
) error {
	_, err := query.DB().NewCreateIndex().
		Model((*SummaryVectorStoreSchema)(nil)).
		Index("sumvecstore_session_id_idx").
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

var messageTableList = []bun.AfterCreateTableHook{
	&MessageVectorStoreSchema{},
	&SummaryVectorStoreSchema{},
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
func enablePgVectorExtension(_ context.Context, db *bun.DB) error {
	// Create pgvector extension if it does not exist
	_, err := db.Exec("CREATE EXTENSION IF NOT EXISTS vector")
	if err != nil {
		return fmt.Errorf("error creating pgvector extension: %w", err)
	}

	// if this is an upgrade, we may need to update the pgvector extension
	// this is a no-op if the extension is already up to date
	// if this fails, Zep may not have rights to update extensions.
	// this is not an issue if running on a managed service.
	_, err = db.Exec("ALTER EXTENSION vector UPDATE")
	if err != nil {
		log.Errorf(
			"error updating pgvector extension: %s. this may happen if running on a managed service without rights to update extensions.",
			err,
		)
		return nil
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

	// apply migrations
	if err := migrations.Migrate(ctx, db); err != nil {
		return fmt.Errorf("failed to apply migrations: %w", err)
	}

	// check that the message and summary embedding dimensions match the configured model
	if err := checkEmbeddingDims(ctx, appState, db, "message", "message_embedding"); err != nil {
		return fmt.Errorf("error checking message embedding dimensions: %w", err)
	}
	if err := checkEmbeddingDims(ctx, appState, db, "summary", "summary_embedding"); err != nil {
		return fmt.Errorf("error checking summary embedding dimensions: %w", err)
	}

	// Create HNSW index on message and summary embeddings if available
	if appState.Config.Store.Postgres.AvailableIndexes.HSNW {
		c := "embedding"
		if err := createHNSWIndex(ctx, db, "message_embedding", c); err != nil {
			return fmt.Errorf("error creating hnsw index: %w", err)
		}

		if err := createHNSWIndex(ctx, db, "summary_embedding", c); err != nil {
			return fmt.Errorf("error creating hnsw index: %w", err)
		}
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
func checkEmbeddingDims(
	ctx context.Context,
	appState *models.AppState,
	db *bun.DB,
	documentType string,
	tableName string,
) error {
	model, err := llms.GetEmbeddingModel(appState, documentType)
	if err != nil {
		return fmt.Errorf("error getting %s embedding model: %w", documentType, err)
	}
	width, err := getEmbeddingColumnWidth(ctx, tableName, db)
	if err != nil {
		return fmt.Errorf("error getting embedding column width: %w", err)
	}

	if width != model.Dimensions {
		log.Warnf(
			"%s embedding dimensions are %d, expected %d.\n migrating %s embedding column width to %d. this may result in loss of existing embedding vectors",
			documentType,
			width,
			model.Dimensions,
			documentType,
			model.Dimensions,
		)
		err := MigrateEmbeddingDims(ctx, db, tableName, model.Dimensions)
		if err != nil {
			return fmt.Errorf("error migrating %s embedding dimensions: %w", documentType, err)
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
		// Something strange has happened. Debug the schema.
		schema, dumpErr := dumpTableSchema(ctx, db, tableName)
		if dumpErr != nil {
			return 0, fmt.Errorf(
				"error getting embedding column width for %s: %w. Original error: %w",
				tableName,
				dumpErr,
				err,
			)
		}
		return 0, fmt.Errorf(
			"error getting embedding column width for %s. Schema: %s: %w",
			tableName,
			schema,
			err,
		)
	}
	return width, nil
}

// dumpTableSchema enables debugging of schema issues
func dumpTableSchema(ctx context.Context, db *bun.DB, tableName string) (string, error) {
	type ColumnInfo struct {
		bun.BaseModel `bun:"table:information_schema.columns" yaml:"-"`
		ColumnName    string         `bun:"column_name"`
		DataType      string         `bun:"data_type"`
		CharMaxLength sql.NullInt32  `bun:"character_maximum_length"`
		ColumnDefault sql.NullString `bun:"column_default"`
		IsNullable    string         `bun:"is_nullable"`
	}

	var columns []ColumnInfo
	err := db.NewSelect().
		Model(&columns).
		Where("table_name = ?", tableName).
		Order("ordinal_position").
		Scan(ctx)
	if err != nil {
		return "", fmt.Errorf("error getting table schema for %s: %w", tableName, err)
	}

	tableSchema := fmt.Sprintf("%+v", columns)

	return tableSchema, nil
}

// MigrateEmbeddingDims drops the old embedding column and creates a new one with the
// correct dimensions.
func MigrateEmbeddingDims(
	ctx context.Context,
	db *bun.DB,
	tableName string,
	dimensions int,
) error {
	// we may be missing a config key, so use the default dimensions if none are provided
	if dimensions == 0 {
		dimensions = defaultEmbeddingDims
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("MigrateEmbeddingDims error starting transaction: %w", err)
	}
	defer rollbackOnError(tx)

	// bun doesn't appear to support IF EXISTS for dropping columns
	columnQuery := `ALTER TABLE ? DROP COLUMN IF EXISTS embedding;
	ALTER TABLE ? ADD COLUMN embedding vector(?);
`
	_, err = tx.ExecContext(
		ctx,
		columnQuery,
		bun.Ident(tableName),
		bun.Ident(tableName),
		dimensions,
	)
	if err != nil {
		return fmt.Errorf("MigrateEmbeddingDims error dropping column embedding: %w", err)
	}

	err = tx.Commit()
	if err != nil {
		return fmt.Errorf("MigrateEmbeddingDims error committing transaction: %w", err)
	}

	return nil
}

// NewPostgresConn creates a new bun.DB connection to a postgres database using the provided DSN.
// The connection is configured to pool connections based on the number of PROCs available.
func NewPostgresConn(appState *models.AppState) (*bun.DB, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

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
	db.AddQueryHook(bunotel.NewQueryHook(bunotel.WithDBName("zep")))

	// Enable pgvector extension
	err := enablePgVectorExtension(ctx, db)
	if err != nil {
		log.Errorf("error enabling pgvector extension: %s", err)
	}

	// IVFFLAT indexes are always available
	appState.Config.Store.Postgres.AvailableIndexes.IVFFLAT = true

	// Check if HNSW indexes are available
	isHNSW, err := isHNSWAvailable(ctx, db)
	if err != nil {
		log.Infof("error checking if hnsw indexes are available: %s", err)
		return nil, err
	}
	if isHNSW {
		appState.Config.Store.Postgres.AvailableIndexes.HSNW = true
	}

	return db, nil
}

// NewPostgresConnForQueue creates a new pgx connection to a postgres database using the provided DSN.
// This connection is intended to be used for queueing tasks.
func NewPostgresConnForQueue(appState *models.AppState) (*sql.DB, error) {
	db, err := sql.Open("pgx", appState.Config.Store.Postgres.DSN)
	if err != nil {
		return nil, err
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
		if errors.Is(err, sql.ErrNoRows) {
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
