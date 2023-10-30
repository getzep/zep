package postgres

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"dario.cat/mergo"
	"github.com/getzep/zep/pkg/models"
	"github.com/uptrace/bun"
)

// mergeMetadata merges the received metadata map with the existing metadata map in DB,
// creating keys and values if they don't exist, and overwriting others.
func mergeMetadata(ctx context.Context,
	db bun.IDB,
	entityField string,
	entityID string,
	table string,
	metadata map[string]interface{},
	isPrivileged bool) (map[string]interface{}, error) {
	if entityField == "" {
		return nil, errors.New("entityField cannot be empty")
	}
	if entityID == "" {
		return nil, errors.New("entityID cannot be empty")
	}
	if table == "" {
		return nil, errors.New("table cannot be empty")
	}
	if len(metadata) == 0 {
		return nil, errors.New("metadata cannot be empty")
	}
	// remove the top-level `system` key from the metadata if the caller is not privileged
	if !isPrivileged {
		delete(metadata, "system")
	}

	// this should include selection of soft-deleted entities
	dbMetadata := new(map[string]interface{})
	err := db.NewSelect().
		Table(table).
		Column("metadata").
		Where("? = ?", bun.Ident(entityField), entityID).
		Scan(ctx, &dbMetadata)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, models.NewNotFoundError(fmt.Sprintf("%s %s", entityField, entityID))
		}
		return nil, fmt.Errorf("failed to get %s: %w", entityField, err)
	}

	// merge the existing metadata with the new metadata
	if err := mergo.Merge(dbMetadata, metadata, mergo.WithOverride); err != nil {
		return nil, fmt.Errorf("failed to merge metadata: %w", err)
	}

	return *dbMetadata, nil
}
