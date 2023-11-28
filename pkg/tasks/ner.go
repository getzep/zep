package tasks

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
)

const NerRetryMax = 3
const NerTimeout = 10 * time.Second

func callNERTask(
	ctx context.Context,
	appState *models.AppState,
	texts []models.TextData,
) (models.EntityResponse, error) {
	url := appState.Config.NLP.ServerURL + "/entities"

	request := make([]models.EntityRequestRecord, len(texts))
	for i, m := range texts {
		r := models.EntityRequestRecord{
			UUID:     m.TextUUID.String(),
			Text:     m.Text,
			Language: "en",
		}
		request[i] = r
	}

	requestBody := models.EntityRequest{Texts: request}
	jsonBody, err := json.Marshal(requestBody)
	if err != nil {
		log.Error("Error marshaling request body:", err)
		return models.EntityResponse{}, err
	}

	var bodyBytes []byte
	var response models.EntityResponse

	client := NewRetryableHTTPClient(NerRetryMax, NerTimeout)

	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		url,
		bytes.NewBuffer(jsonBody),
	)
	if err != nil {
		return models.EntityResponse{}, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return models.EntityResponse{}, err
	}
	defer func(Body io.ReadCloser) {
		err := Body.Close()
		if err != nil {
			log.Errorf("Error closing response body: %s", err)
		}
	}(resp.Body)

	if resp.StatusCode != http.StatusOK {
		errorString := fmt.Sprintf(
			"Error making POST request: %d - %s",
			resp.StatusCode,
			resp.Status,
		)
		return models.EntityResponse{}, fmt.Errorf(errorString)
	}

	bodyBytes, err = io.ReadAll(resp.Body)
	if err != nil {
		return models.EntityResponse{}, err
	}

	err = json.Unmarshal(bodyBytes, &response)
	if err != nil {
		return models.EntityResponse{}, err
	}

	return response, nil
}

func extractEntities(entities interface{}) []map[string]interface{} {
	entityMapWithDataKey := internal.StructToMap(entities)
	if data, ok := entityMapWithDataKey["data"]; ok {
		entities := data.([]interface{})
		entityList := make([]map[string]interface{}, len(entities))
		for i, entity := range entities {
			entityList[i] = entity.(map[string]interface{})
		}
		return entityList
	}

	return nil
}
