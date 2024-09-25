package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"dario.cat/mergo"
	"github.com/getzep/zep/lib/zerrors"
	"github.com/uptrace/bun"
)

type mergeMetadataRequest struct {
	entityField string
	entityID    string
	table       string
	metadata    map[string]any
}

// mergeMetadata merges the received metadata map with the existing metadata map in DB,
// creating keys and values if they don't exist, and overwriting others.
func mergeMetadata(ctx context.Context,
	db bun.IDB,
	schemaName string,
	mergeData mergeMetadataRequest,
	isPrivileged bool,
) (map[string]any, error) {
	if mergeData.entityField == "" {
		return nil, errors.New("entityField cannot be empty")
	}

	if mergeData.entityID == "" {
		return nil, errors.New("entityID cannot be empty")
	}

	if mergeData.table == "" {
		return nil, errors.New("table cannot be empty")
	}

	if len(mergeData.metadata) == 0 {
		return nil, errors.New("metadata cannot be empty")
	}

	// remove the top-level `system` key from the metadata if the caller is not privileged
	if !isPrivileged {
		delete(mergeData.metadata, "system")
	}

	// this should include selection of soft-deleted entities
	dbMetadata := new(map[string]any)

	err := db.NewSelect().
		Table(fmt.Sprintf("%s.%s", schemaName, mergeData.table)).
		Column("metadata").
		Where("? = ?", bun.Ident(mergeData.entityField), mergeData.entityID).
		Scan(ctx, &dbMetadata)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, zerrors.NewNotFoundError(
				fmt.Sprintf("%s %s", mergeData.entityField, mergeData.entityID),
			)
		}
		return nil, fmt.Errorf("failed to get %s: %w", mergeData.entityField, err)
	}

	// merge the existing metadata with the new metadata
	if err := mergo.Merge(dbMetadata, mergeData.metadata, mergo.WithOverride); err != nil {
		return nil, fmt.Errorf("failed to merge metadata: %w", err)
	}

	return *dbMetadata, nil
}
