package llms

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/avast/retry-go/v4"

	"github.com/getzep/zep/pkg/models"
)

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

	var bodyBytes []byte
	// Retry POST request to entity extractor 3 times with 1 second delay.
	err = retry.Do(
		func() error {
			var err error
			bodyBytes, err = makeEmbedRequest(ctx, url, jsonBody)
			if err != nil {
				return err
			}
			return nil
		},
		retry.Attempts(3),
		retry.Delay(time.Second),
	)
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

func makeEmbedRequest(ctx context.Context, url string, jsonBody []byte) ([]byte, error) {
	httpClient := &http.Client{Timeout: 30 * time.Second}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewBuffer(jsonBody))
	if err != nil {
		return nil, err
	}

	req.Header.Set("Content-Type", "application/json")
	resp, err := httpClient.Do(req)
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
