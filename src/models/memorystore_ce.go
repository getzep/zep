
package models

type MemoryStore interface {
	MemoryStoreCommon
}

type SessionStorer interface {
	SessionStorerCommon
}

type MessageStorer interface {
	MessageStorerCommon
}

type MemoryStorer interface {
	MemoryStorerCommon
}
