package apidata

import (
	"time"

	"github.com/google/uuid"

	"github.com/getzep/zep/models"
)

func FactListTransformer(facts []models.Fact) []Fact {
	f := make([]Fact, len(facts))
	for i, fact := range facts {
		f[i] = FactTransformer(fact)
	}

	return f
}

func FactTransformerPtr(fact *models.Fact) *Fact {
	if fact == nil {
		return nil
	}

	f := FactTransformer(*fact)

	return &f
}

func FactTransformer(fact models.Fact) Fact {
	return Fact{
		UUID:      fact.UUID,
		CreatedAt: fact.CreatedAt,
		Fact:      fact.Fact,
		Rating:    fact.Rating,
	}
}

type Fact struct {
	UUID      uuid.UUID `json:"uuid"`
	CreatedAt time.Time `json:"created_at"`
	Fact      string    `json:"fact"`
	Rating    *float64  `json:"rating,omitempty"`
}

type FactsResponse struct {
	Facts []Fact `json:"facts"`
}

type FactResponse struct {
	Fact Fact `json:"fact"`
}
