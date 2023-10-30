package tasks

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/avast/retry-go/v4"
	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
)

func callNERTask(
	_ context.Context,
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

	var resp *http.Response
	var bodyBytes []byte
	var response models.EntityResponse

	// Retry POST request to entity extractor 3 times with 1 second delay.
	err = retry.Do(
		func() error {
			var err error
			resp, err = http.Post(url, "application/json", bytes.NewBuffer(jsonBody)) //nolint:gosec
			if err != nil {
				log.Error("Error making POST request:", err)
				return err
			}
			defer resp.Body.Close()

			bodyBytes, err = io.ReadAll(resp.Body)
			if err != nil {
				log.Error("Error reading response body:", err)
				return err
			}

			err = json.Unmarshal(bodyBytes, &response)
			if err != nil {
				fmt.Println("Error unmarshaling response body:", err)
				return err
			}
			return nil
		},
		retry.Attempts(3),
		retry.Delay(time.Second),
	)

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
