package store

// BaseMemoryStore is the base implementation of a MemoryStore. Client is the underlying datastore client, such as a
// database connection.
type BaseMemoryStore[T any] struct {
	Client T
}
