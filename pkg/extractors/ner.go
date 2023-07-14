package extractors

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

	"github.com/google/uuid"

	"github.com/getzep/zep/pkg/models"
)

// Force compiler to validate that the Extractor implements the Extractor interface.
var _ models.Extractor = &EntityExtractor{}

type EntityExtractor struct {
	BaseExtractor
}

func NewEntityExtractor() *EntityExtractor {
	return &EntityExtractor{}
}

func (ee *EntityExtractor) Extract(
	ctx context.Context,
	appState *models.AppState,
	messageEvent *models.MessageEvent,
) error {
	sessionID := messageEvent.SessionID
	sessionMutex := ee.getSessionMutex(sessionID)
	sessionMutex.Lock()
	defer sessionMutex.Unlock()

	nerResponse, err := callEntityExtractor(ctx, appState, messageEvent.Messages)
	if err != nil {
		return NewExtractorError("EntityExtractor extract entities call failed", err)
	}

	messages := make([]models.Message, len(nerResponse.Texts))
	for i, r := range nerResponse.Texts {
		msgUUID, err := uuid.Parse(r.UUID)
		if err != nil {
			return NewExtractorError("EntityExtractor failed to parse message UUID", err)
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

	err = appState.MemoryStore.PutMessageMetadata(ctx, appState, sessionID, messages, true)
	if err != nil {
		return NewExtractorError("EntityExtractor failed to put message metadata", err)
	}

	return nil
}

func (ee *EntityExtractor) Notify(
	ctx context.Context,
	appState *models.AppState,
	messageEvents *models.MessageEvent,
) error {
	if messageEvents == nil {
		return NewExtractorError(
			"EntityExtractor message events is nil at Notify",
			nil,
		)
	}
	log.Debugf("EntityExtractor notify: %d messages", len(messageEvents.Messages))
	go func() {
		err := ee.Extract(ctx, appState, messageEvents)
		if err != nil {
			log.Error(fmt.Sprintf("EntityExtractor extract failed: %v", err))
		}
	}()
	return nil
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

func callEntityExtractor(
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
