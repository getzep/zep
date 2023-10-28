package search

import (
	"errors"
	"fmt"
	"math"

	"github.com/getzep/zep/internal"
	"github.com/viterin/vek"
	"github.com/viterin/vek/vek32"
)

var log = internal.GetLogger()

func init() {
	log.Infof("MMR acceleration status: %v", vek.Info())
}

// pairwiseCosineSimilarity takes two matrices of vectors and returns a matrix, where
// the value at [i][j] is the cosine similarity between the ith vector in matrix1 and
// the jth vector in matrix2.
func pairwiseCosineSimilarity(matrix1 [][]float32, matrix2 [][]float32) ([][]float32, error) {
	result := make([][]float32, len(matrix1))
	for i, vec1 := range matrix1 {
		result[i] = make([]float32, len(matrix2))
		for j, vec2 := range matrix2 {
			if len(vec1) != len(vec2) {
				return nil, fmt.Errorf(
					"vector lengths do not match: %d != %d",
					len(vec1),
					len(vec2),
				)
			}
			result[i][j] = vek32.CosineSimilarity(vec1, vec2)
		}
	}
	return result, nil
}

// MaximalMarginalRelevance implements the Maximal Marginal Relevance algorithm.
// It takes a query embedding, a list of embeddings, a lambda multiplier, and a
// number of results to return. It returns a list of indices of the embeddings
// that are most relevant to the query.
// See https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf
// Implementation borrowed from LangChain
// https://github.com/langchain-ai/langchain/blob/4a2f0c51a116cc3141142ea55254e270afb6acde/libs/langchain/langchain/vectorstores/utils.py
func MaximalMarginalRelevance(
	queryEmbedding []float32,
	embeddingList [][]float32,
	lambdaMult float32,
	k int,
) ([]int, error) {
	// if either k or the length of the embedding list is 0, return an empty list
	if min(k, len(embeddingList)) <= 0 {
		return []int{}, nil
	}

	// We expect the query embedding and the embeddings in the list to have the same width
	if len(queryEmbedding) != len(embeddingList[0]) {
		return []int{}, errors.New("query embedding width does not match embedding vector width")
	}

	similarityToQueryMatrix, err := pairwiseCosineSimilarity(
		[][]float32{queryEmbedding},
		embeddingList,
	)
	if err != nil {
		return nil, err
	}
	similarityToQuery := similarityToQueryMatrix[0]

	mostSimilar := vek32.ArgMax(similarityToQuery)
	idxs := []int{mostSimilar}
	selected := [][]float32{embeddingList[mostSimilar]}

	for len(idxs) < min(k, len(embeddingList)) {
		var bestScore float32 = -math.MaxFloat32
		idxToAdd := -1
		similarityToSelected, err := pairwiseCosineSimilarity(embeddingList, selected)
		if err != nil {
			return nil, err
		}

		for i, queryScore := range similarityToQuery {
			if contains(idxs, i) {
				continue
			}
			redundantScore := vek32.Max(similarityToSelected[i])
			equationScore := lambdaMult*queryScore - (1-lambdaMult)*redundantScore
			if equationScore > bestScore {
				bestScore = equationScore
				idxToAdd = i
			}
		}
		idxs = append(idxs, idxToAdd)
		selected = append(selected, embeddingList[idxToAdd])
	}
	return idxs, nil
}

// contains returns true if the slice contains the value
func contains(slice []int, val int) bool {
	for _, item := range slice {
		if item == val {
			return true
		}
	}
	return false
}
