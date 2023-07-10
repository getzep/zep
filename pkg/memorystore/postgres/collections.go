package postgres

import (
	"errors"
	"fmt"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
)

func generateCollectionTableName(collection *models.DocumentCollection) (string, error) {
	if collection == nil {
		return "", errors.New("collection is nil")
	}
	if collection.UUID == uuid.Nil {
		return "", errors.New("collection.UUID is nil")
	}
	if collection.Name == "" {
		return "", errors.New("collection.Name is empty")
	}
	if len(collection.Name) > 47 {
		return "", fmt.Errorf(
			"collection name too long: %d > 47 char maximum",
			len(collection.Name),
		)
	}
	if collection.EmbeddingDimensions == 0 {
		return "", errors.New("collection.EmbeddingDimensions is 0")
	}
	tableName := fmt.Sprintf(
		"docstore_%s_%d",
		collection.Name,
		collection.EmbeddingDimensions,
	)
	if len(tableName) > 63 {
		return "", fmt.Errorf("table name too long: %d > 63 char maximum", len(tableName))
	}
	return tableName, nil
}
