package search

import (
	"fmt"
	"math"

	"gonum.org/v1/gonum/floats"

	"gonum.org/v1/gonum/mat"
)

// CosineSimilarity calculates the cosine similarity between two vectors.
// The vectors must be of the same length.
func CosineSimilarity(X, Y *mat.Dense) (*mat.Dense, error) { // nolint: gocritic
	rX, cX := X.Dims()
	rY, cY := Y.Dims()

	if rX == 0 || rY == 0 {
		return mat.NewDense(0, 0, nil), nil
	}

	if cX != cY {
		return nil, fmt.Errorf(
			"number of columns in X and Y must be the same. X has shape [%d, %d] and Y has shape [%d, %d]",
			rX,
			cX,
			rY,
			cY,
		)
	}

	Xnorm := mat.NewVecDense(rX, nil)
	Ynorm := mat.NewVecDense(rY, nil)

	for i := 0; i < rX; i++ {
		Xnorm.SetVec(i, mat.Norm(X.RowView(i), 2))
	}

	for i := 0; i < rY; i++ {
		Ynorm.SetVec(i, mat.Norm(Y.RowView(i), 2))
	}

	var XT mat.Dense
	XT.CloneFrom(X.T())

	similarity := mat.NewDense(rX, rY, nil)
	similarity.Product(X, &XT)

	for i := 0; i < rX; i++ {
		for j := 0; j < rY; j++ {
			val := similarity.At(i, j) / (Xnorm.AtVec(i) * Ynorm.AtVec(j))
			if math.IsNaN(val) || math.IsInf(val, 0) {
				val = 0.0
			}
			similarity.Set(i, j, val)
		}
	}

	return similarity, nil
}

// MaximalMarginalRelevance implements the Maximal Marginal Relevance algorithm.
// It takes a query embedding, a list of embeddings, a lambda multiplier, and a
// number of results to return. It returns a list of indices of the embeddings
// that are most relevant to the query.
// This is a relatively naive and unoptimized implementation of MMR. :-/
// See https://www.cs.cmu.edu/~jgc/publication/The_Use_MMR_Diversity_Based_LTMIR_1998.pdf
func MaximalMarginalRelevance(
	queryEmbedding *mat.Dense,
	embeddingList *mat.Dense,
	lambdaMult float64,
	k int,
) ([]int, error) {
	rEmbed, _ := embeddingList.Dims()
	if k <= 0 || rEmbed == 0 {
		return []int{}, nil
	}

	var mostSimilar int
	var bestScore float64
	var idxToAdd int

	similarityToQuery, err := CosineSimilarity(queryEmbedding, embeddingList)
	if err != nil {
		return nil, err
	}
	mostSimilar = floats.MaxIdx(similarityToQuery.RawMatrix().Data)
	idxs := []int{mostSimilar}
	selected := mat.DenseCopyOf(embeddingList.RowView(mostSimilar))

	for len(idxs) < min(k, rEmbed) {
		bestScore = math.Inf(-1)
		idxToAdd = -1
		r, c := selected.Dims()
		selectedTransposed := mat.NewDense(c, r, nil)
		selectedTransposed.CloneFrom(selected.T())
		similarityToSelected, err := CosineSimilarity(embeddingList, selectedTransposed)
		if err != nil {
			return nil, err
		}
		for i, queryScore := range similarityToQuery.RawMatrix().Data {
			if contains(idxs, i) {
				continue
			}
			redundantScore := floats.Max(similarityToSelected.RawMatrix().Data)
			equationScore := lambdaMult*queryScore - (1-lambdaMult)*redundantScore
			if equationScore > bestScore {
				bestScore = equationScore
				idxToAdd = i
			}
		}
		idxs = append(idxs, idxToAdd)
		selected.Stack(selected, embeddingList.RowView(idxToAdd))
	}
	return idxs, nil
}

func contains(slice []int, val int) bool {
	for _, item := range slice {
		if item == val {
			return true
		}
	}
	return false
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
