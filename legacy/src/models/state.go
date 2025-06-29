package models

import (
	"github.com/google/uuid"

	"github.com/getzep/zep/lib/enablement"
	"github.com/getzep/zep/lib/pg"
)

type AppStateCommon struct {
	DB            pg.Connection
	TaskRouter    TaskRouter
	TaskPublisher TaskPublisher
}

type RequestStateCommon struct {
	Memories MemoryStore
	Users    UserStore
	Sessions SessionStore

	ProjectUUID uuid.UUID
	SessionUUID uuid.UUID

	EnablementProfile enablement.Profile

	SchemaName       string
	RequestTokenType string
}

func (rs *RequestState) GetProjectUUID() uuid.UUID {
	return rs.ProjectUUID
}

func (rs *RequestState) GetRequestTokenType() string {
	return rs.RequestTokenType
}
