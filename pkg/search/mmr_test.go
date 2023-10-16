package search

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestMaximalMarginalRelevance(t *testing.T) {
	// Test case for mismatched vector widths
	t.Run("MismatchedVectorWidths", func(t *testing.T) {
		queryEmbedding := []float32{0.1, 0.2, 0.3, 0.4, 0.5}
		embeddingList := [][]float32{
			{0.1, 0.2, 0.3},
			{0.2, 0.3, 0.4, 0.5, 0.5},
		}
		_, err := MaximalMarginalRelevance(queryEmbedding, embeddingList, 0.5, 2)
		assert.Error(t, err)
	})

	// Test case for checking ranking
	t.Run("Ranking", func(t *testing.T) {
		queryEmbedding := []float32{0.1, 0.2, 0.3, 0.4, 0.5}
		embeddingList := [][]float32{
			{0.1, 0.2, 0.3, 0.4, 0.4},
			{0.2, 0.3, 0.4, 0.5, 0.5},
			{0.1, 0.2, 0.3, 0.4, 0.6},
			{0.1, 0.0, 0.0, 0.0, 0.0},
			{0.2, 0.0, 0.0, 0.0, 0.0},
		}
		expected := []int{2, 1}
		result, err := MaximalMarginalRelevance(queryEmbedding, embeddingList, 0.5, 2)
		assert.NoError(t, err)
		assert.Equal(t, expected, result)
	})

	// Test case for modifying lambda
	t.Run("LambdaModification", func(t *testing.T) {
		queryEmbedding := []float32{0.1, 0.2, 0.3, 0.4, 0.5}
		embeddingList := [][]float32{
			{0.1, 0.2, 0.3, 0.4, 0.4},
			{0.2, 0.3, 0.4, 0.5, 0.5},
			{0.1, 0.2, 0.3, 0.4, 0.6},
		}
		expected := []int{2, 0}
		result, err := MaximalMarginalRelevance(queryEmbedding, embeddingList, 1.0, 2)
		assert.NoError(t, err)
		assert.Equal(t, expected, result)
	})
}
