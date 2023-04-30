package main

import "database/sql"

type PostgresDataStore struct {
	BaseDataStore[*sql.DB]
}

func NewPostgresDataStore(db *sql.DB) *PostgresDataStore {
	return &PostgresDataStore{BaseDataStore[*sql.DB]{client: db}}
}

// Implement the DataStore interface methods for PostgreSQL.
// Implement the methods similar to RedisDataStore, but with PostgreSQL logic.
