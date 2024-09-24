package store

import (
	"context"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/lib/pg"
	"github.com/getzep/zep/models"
	"github.com/getzep/zep/store/migrations"
)

const SearchPathSuffix = ", public"

type BaseSchema struct {
	SchemaName string `bun:"-" yaml:"schema_name"`
	TableName  string `bun:"-"`
	Alias      string `bun:"-"`
}

func (s *BaseSchema) GetTableName() string {
	return fmt.Sprintf("%s.%s", s.SchemaName, s.TableName)
}

func (s *BaseSchema) GetTableAndAlias() string {
	return fmt.Sprintf("%s AS %s", s.GetTableName(), s.Alias)
}

func NewBaseSchema(schemaName, tableName string) BaseSchema {
	return BaseSchema{
		SchemaName: schemaName,
		TableName:  tableName,
	}
}

type SessionSchema struct {
	bun.BaseModel `bun:"table:sessions,alias:s" yaml:"-"`
	BaseSchema    `yaml:"-"`

	SessionSchemaExt `bun:",extend"`

	UUID      uuid.UUID      `bun:",pk,type:uuid,default:gen_random_uuid()"                     yaml:"uuid,omitempty"`
	ID        int64          `bun:",autoincrement"                                              yaml:"id,omitempty"` // used as a cursor for pagination
	SessionID string         `bun:",unique,notnull"                                             yaml:"session_id,omitempty"`
	CreatedAt time.Time      `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp" yaml:"created_at,omitempty"`
	UpdatedAt time.Time      `bun:"type:timestamptz,nullzero,notnull,default:current_timestamp" yaml:"updated_at,omitempty"`
	DeletedAt time.Time      `bun:"type:timestamptz,soft_delete,nullzero"                       yaml:"deleted_at,omitempty"`
	EndedAt   *time.Time     `bun:"type:timestamptz,nullzero"                                   yaml:"ended_at,omitempty"`
	Metadata  map[string]any `bun:"type:jsonb,nullzero,json_use_number"                         yaml:"metadata,omitempty"`
	// UserUUID must be pointer type in order to be nullable
	UserID      *string     `bun:","                                                         yaml:"user_id,omitempty"`
	User        *UserSchema `bun:"rel:belongs-to,join:user_id=user_id,on_delete:cascade"     yaml:"-"`
	ProjectUUID uuid.UUID   `bun:"type:uuid,notnull"                                         yaml:"project_uuid,omitempty"`
}

func (s *SessionSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		s.UpdatedAt = time.Now()
	}
	return nil
}

type MessageStoreSchema struct {
	bun.BaseModel `bun:"table:messages,alias:m" yaml:"-"`
	BaseSchema    `yaml:"-"`

	UUID uuid.UUID `bun:",pk,type:uuid,default:gen_random_uuid()"                     yaml:"uuid"`
	// ID is used only for sorting / slicing purposes as we can't sort by CreatedAt for messages created simultaneously
	ID          int64           `bun:",autoincrement"                                              yaml:"id,omitempty"`
	CreatedAt   time.Time       `bun:"type:timestamptz,notnull,default:current_timestamp"          yaml:"created_at,omitempty"`
	UpdatedAt   time.Time       `bun:"type:timestamptz,nullzero,default:current_timestamp"         yaml:"updated_at,omitempty"`
	DeletedAt   time.Time       `bun:"type:timestamptz,soft_delete,nullzero"                       yaml:"deleted_at,omitempty"`
	SessionID   string          `bun:",notnull"                                                    yaml:"session_id,omitempty"`
	ProjectUUID uuid.UUID       `bun:"type:uuid,notnull"                                           yaml:"project_uuid,omitempty"`
	Role        string          `bun:",notnull"                                                    yaml:"role,omitempty"`
	RoleType    models.RoleType `bun:",type:public.role_type_enum,nullzero,default:'norole'" yaml:"role_type,omitempty"`
	Content     string          `bun:",notnull"                                                    yaml:"content,omitempty"`
	TokenCount  int             `bun:",notnull"                                                    yaml:"token_count,omitempty"`
	Metadata    map[string]any  `bun:"type:jsonb,nullzero,json_use_number"                         yaml:"metadata,omitempty"`
	Session     *SessionSchema  `bun:"rel:belongs-to,join:session_id=session_id,on_delete:cascade" yaml:"-"`
}

func (s *MessageStoreSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		s.UpdatedAt = time.Now()
	}
	return nil
}

type UserSchema struct {
	bun.BaseModel `bun:"table:users,alias:u" yaml:"-"`
	BaseSchema    `yaml:"-"`

	UserSchemaExt `bun:",extend"`

	UUID        uuid.UUID      `bun:",pk,type:uuid,default:gen_random_uuid()"             yaml:"uuid,omitempty"`
	ID          int64          `bun:",autoincrement"                                      yaml:"id,omitempty"` // used as a cursor for pagination
	CreatedAt   time.Time      `bun:"type:timestamptz,notnull,default:current_timestamp"  yaml:"created_at,omitempty"`
	UpdatedAt   time.Time      `bun:"type:timestamptz,nullzero,default:current_timestamp" yaml:"updated_at,omitempty"`
	DeletedAt   time.Time      `bun:"type:timestamptz,soft_delete,nullzero"               yaml:"deleted_at,omitempty"`
	UserID      string         `bun:",unique,notnull"                                     yaml:"user_id,omitempty"`
	Email       string         `bun:","                                                   yaml:"email,omitempty"`
	FirstName   string         `bun:","                                                   yaml:"first_name,omitempty"`
	LastName    string         `bun:","                                                   yaml:"last_name,omitempty"`
	ProjectUUID uuid.UUID      `bun:"type:uuid,notnull"                                                           yaml:"project_uuid,omitempty"`
	Metadata    map[string]any `bun:"type:jsonb,nullzero,json_use_number"                 yaml:"metadata,omitempty"`
}

func (u *UserSchema) BeforeAppendModel(_ context.Context, query bun.Query) error {
	if _, ok := query.(*bun.UpdateQuery); ok {
		u.UpdatedAt = time.Now()
	}
	return nil
}

type indexInfo struct {
	model           any
	column          string
	indexName       string
	compositeColumn []string
	unique          bool   //nolint:unused // unused
	custom          string //nolint:unused // unused
}

var (
	// messageTableList is a list of tables that are created when the schema is created.
	// the list is also used when deleting message-related rows from the database.
	// DO NOT USE this directly. Use messageTableList instead.
	__messageTableList = []any{
		&MessageStoreSchema{},
		&SessionSchema{},
	}

	// DO NOT USE this directly. Use bunModels instead.
	__bunModels = []any{
		&UserSchema{},
		&MessageStoreSchema{},
		&SessionSchema{},
	}

	__embeddingTables = []string{}

	// DO NOT USE this directly. Use indexes instead.
	__indexes = []indexInfo{
		{model: &UserSchema{}, column: "user_id", indexName: "user_user_id_idx"},
		{model: &UserSchema{}, column: "email", indexName: "user_email_idx"},
		{model: &MessageStoreSchema{}, column: "session_id", indexName: "memstore_session_id_idx"},
		{model: &MessageStoreSchema{}, column: "id", indexName: "memstore_id_idx"},
		{
			model:           &MessageStoreSchema{},
			compositeColumn: []string{"session_id", "project_uuid", "deleted_at"},
			indexName:       "memstore_session_id_project_uuid_deleted_at_idx",
		},
		{model: &SessionSchema{}, column: "user_id", indexName: "session_user_id_idx"},
		{
			model:           &SessionSchema{},
			compositeColumn: []string{"session_id", "project_uuid", "deleted_at"},
			indexName:       "session_id_project_uuid_deleted_at_idx",
		},
	}
)

func MigrateSchema(ctx context.Context, db pg.Connection, schemaName string) error {
	if err := migrations.Migrate(ctx, db, schemaName); err != nil {
		return fmt.Errorf("failed to apply migrations: %w", err)
	}

	return nil
}
