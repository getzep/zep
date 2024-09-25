
package store

import "github.com/uptrace/bun"

type SessionSchemaExt struct {
	bun.BaseModel `bun:"table:sessions,alias:s" yaml:"-"`
}

type UserSchemaExt struct {
	bun.BaseModel `bun:"table:users,alias:u" yaml:"-"`
}

var (
	indexes          = __indexes
	messageTableList = __messageTableList
	bunModels        = __bunModels
	embeddingTables  = __embeddingTables

	_ = indexes
	_ = __indexes
	_ = messageTableList
	_ = __messageTableList
	_ = bunModels
	_ = __bunModels
	_ = embeddingTables
	_ = __embeddingTables
)
