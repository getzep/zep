package search

import (
	"slices"

	"github.com/google/uuid"
)

type Rankable interface {
	GetUUID() uuid.UUID
}

// ReciprocalRankFusion is a function that takes a list of result sets and returns a single list of results,
// where each result is ranked by the sum of the reciprocal ranks of the results in each result set.
func ReciprocalRankFusion[T Rankable](results [][]T) []T {
	rankings := make(map[uuid.UUID]float64)
	for _, resultSet := range results {
		for rank, result := range resultSet {
			rankings[result.GetUUID()] += 1.0 / float64(rank+1) //nolint:revive //declaring consts here would be silly
		}
	}

	uniqueResults := make(map[uuid.UUID]T)
	for _, resultSet := range results {
		for _, result := range resultSet {
			id := result.GetUUID()
			if _, exists := uniqueResults[id]; !exists {
				uniqueResults[id] = result
			}
		}
	}

	finalResults := make([]T, 0, len(uniqueResults))
	for _, item := range uniqueResults {
		finalResults = append(finalResults, item)
	}

	slices.SortFunc(finalResults, func(a, b T) int {
		if rankings[a.GetUUID()] > rankings[b.GetUUID()] {
			return -1
		} else if rankings[a.GetUUID()] < rankings[b.GetUUID()] {
			return 1
		}
		return 0
	})

	return finalResults
}
