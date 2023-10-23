package tasks

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/ThreeDotsLabs/watermill/message"
	"github.com/avast/retry-go/v4"

	"github.com/getzep/zep/internal"

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
)

var _ models.Task = &NERTask{}

type NERTask struct {
	appState *models.AppState
}

func (n *NERTask) Execute(
	ctx context.Context,
	msg *message.Message,
) error {
	sessionID := msg.Metadata.Get("session_id")
	if sessionID == "" {
		return errors.New("NERTask session_id is empty")
	}

	log.Debugf("NERTask called for session %s", sessionID)

	var msgs []models.Message
	err := json.Unmarshal(msg.Payload, &msgs)
	if err != nil {
		return err
	}

	nerResponse, err := callNERTask(ctx, n.appState, msgs)
	if err != nil {
		return fmt.Errorf("NERTask extract entities call failed: %w", err)
	}

	messages := make([]models.Message, len(nerResponse.Texts))
	for i, r := range nerResponse.Texts {
		msgUUID, err := uuid.Parse(r.UUID)
		if err != nil {
			return fmt.Errorf("NERTask failed to parse message UUID: %w", err)
		}
		entityList := extractEntities(r.Entities)

		if len(entityList) == 0 {
			continue
		}

		messages[i] = models.Message{
			UUID: msgUUID,
			Metadata: map[string]interface{}{
				"system": map[string]interface{}{"entities": entityList},
			},
		}
	}

	err = n.appState.MemoryStore.PutMessageMetadata(ctx, n.appState, sessionID, messages, true)
	if err != nil {
		return fmt.Errorf("NERTask failed to put message metadata: %w", err)
	}

	return nil
}

func (n *NERTask) HandleError(err error) {
	log.Errorf("NERTask error: %s", err)
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

func callNERTask(
	_ context.Context,
	appState *models.AppState,
	messages []models.Message,
) (models.EntityResponse, error) {
	url := appState.Config.NLP.ServerURL + "/entities"

	request := make([]models.EntityRequestRecord, len(messages))
	for i, m := range messages {
		r := models.EntityRequestRecord{
			UUID:     m.UUID.String(),
			Text:     m.Content,
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
