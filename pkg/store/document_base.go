package store

// BaseDocumentStore is the base implementation of a DocumentStore. Client is the underlying datastore client,
// such as a database connection.
type BaseDocumentStore[T any] struct {
	Client T
}
