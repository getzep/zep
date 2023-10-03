package llms

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/hashicorp/go-retryablehttp"

	"github.com/getzep/zep/pkg/models"
)

const MaxLocalEmbedderRetryAttempts = 5
const LocalEmbedderTimeout = 60 * time.Second

// embedTextsLocal embeds a slice of texts using the local embeddings service
func embedTextsLocal(
	ctx context.Context,
	appState *models.AppState,
	documentType string,
	texts []string,
) ([][]float32, error) {
	if len(texts) == 0 {
		return nil, nil
	}

	if documentType != "message" && documentType != "document" {
		return nil, fmt.Errorf("invalid document type: %s", documentType)
	}

	url := appState.Config.NLP.ServerURL + "/embeddings/" + documentType

	documents := make([]models.MessageEmbedding, len(texts))
	for i, text := range texts {
		documents[i] = models.MessageEmbedding{Text: text}
	}
	collection := models.MessageEmbeddingCollection{
		Embeddings: documents,
	}
	jsonBody, err := json.Marshal(collection)
	if err != nil {
		log.Error("Error marshaling request body:", err)
		return nil, err
	}

	bodyBytes, err := makeEmbedRequest(ctx, url, jsonBody)
	if err != nil {
		return nil, err
	}

	err = json.Unmarshal(bodyBytes, &collection)
	if err != nil {
		log.Errorf("Error unmarshaling response body: %s", err)
		return nil, err
	}

	m := make([][]float32, len(collection.Embeddings))
	for i := range collection.Embeddings {
		m[i] = collection.Embeddings[i].Embedding
	}

	return m, nil
}

// makeEmbedRequest makes a POST request to the local embeddings service. It
// returns the response body as a byte slice. A retryablehttp.Client is used to
// make the request.
func makeEmbedRequest(ctx context.Context, url string, jsonBody []byte) ([]byte, error) {
	// we set both the context and the request timeout (below) to the same value
	// so that the request will be cancelled if the context times out and/or the
	// request times out
	ctx, cancel := context.WithTimeout(ctx, LocalEmbedderTimeout)
	defer cancel()

	retryableHTTPClient := NewRetryableHTTPClient(
		MaxLocalEmbedderRetryAttempts,
		LocalEmbedderTimeout,
	)

	req, err := retryablehttp.NewRequestWithContext(
		ctx,
		http.MethodPost,
		url,
		bytes.NewBuffer(jsonBody),
	)
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "application/json")
	resp, err := retryableHTTPClient.Do(req)
	if err != nil {
		log.Error("Error making POST request:", err)
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		errorString := fmt.Sprintf(
			"Error making POST request: %d - %s",
			resp.StatusCode,
			resp.Status,
		)
		log.Error(errorString)
		return nil, fmt.Errorf(errorString)
	}

	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Error("Error reading response body:", err)
		return nil, err
	}

	return bodyBytes, nil
}
