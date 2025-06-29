
package apihandlers

import (
	"context"

	"github.com/google/uuid"

	"github.com/getzep/zep/lib/graphiti"
	"github.com/getzep/zep/models"
)

func getFact(ctx context.Context, factUUID uuid.UUID, _ *models.RequestState) (*models.Fact, error) {
	graphFact, err := graphiti.I().GetFact(ctx, factUUID)
	if err != nil {
		return nil, err
	}

	return &models.Fact{
		UUID:      graphFact.UUID,
		Fact:      graphFact.Fact,
		CreatedAt: graphFact.ExtractCreatedAt(),
	}, nil
}

func deleteSessionFact(ctx context.Context, factUUID uuid.UUID, _ *models.RequestState) error {
	return graphiti.I().DeleteFact(ctx, factUUID)
}
