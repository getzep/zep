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

	messageMetaSet := make([]models.MessageMetadata, len(nerResponse.Texts), 0)
	for i, r := range nerResponse.Texts {
		msgUUID, err := uuid.Parse(r.UUID)
		if err != nil {
			return NewExtractorError("Can't parse message UUID", err)
		}
		messageMetaSet[i].UUID = msgUUID
		messageMetaSet[i].Key = "system"
		messageMetaSet[i].Metadata = map[string]interface{}{
			"entities": internal.StructToMap(r.Entities),
		}
	}
	err = appState.MemoryStore.PutMessageMetadata(ctx, appState, sessionID, messageMetaSet, true)
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

func callEntityExtractor(
	_ context.Context,
	appState *models.AppState,
	messages []models.Message,
) (EntityResponse, error) {
	url := appState.Config.NLP.ServerURL + "/entities"

	request := make([]EntityRequestRecord, len(messages))
	for i, m := range messages {
		r := EntityRequestRecord{
			UUID:     m.UUID.String(),
			Text:     m.Content,
			Language: "en",
		}
		request[i] = r
	}

	requestBody := EntityRequest{Texts: request}
	jsonBody, err := json.Marshal(requestBody)
	if err != nil {
		log.Error("Error marshaling request body:", err)
		return EntityResponse{}, err
	}

	var resp *http.Response
	var bodyBytes []byte
	var response EntityResponse

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
		retry.Attempts(3),        // Adjust to your needs
		retry.Delay(time.Second), // Adjust to your needs
	)

	if err != nil {
		return EntityResponse{}, err
	}

	return response, nil
}

type HTTPValidationError struct {
	Detail []ValidationError `json:"detail"`
}

type EntityMatch struct {
	Start int    `json:"start"`
	End   int    `json:"end"`
	Text  string `json:"text"`
}

type Entity struct {
	Name    string        `json:"name"`
	Label   string        `json:"label"`
	Matches []EntityMatch `json:"matches"`
}

type EntityRequestRecord struct {
	UUID     string `json:"uuid"`
	Text     string `json:"text"`
	Language string `json:"language"`
}

type EntityResponseRecord struct {
	UUID     string   `json:"uuid"`
	Entities []Entity `json:"entities"`
}

type EntityRequest struct {
	Texts []EntityRequestRecord `json:"texts"`
}

type EntityResponse struct {
	Texts []EntityResponseRecord `json:"texts"`
}

type ValidationError struct {
	Loc  []interface{} `json:"loc"`
	Msg  string        `json:"msg"`
	Type string        `json:"type"`
}
