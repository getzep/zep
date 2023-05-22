package extractors

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"

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

	log.Debugf("EntityExtractor received %d entities: %+v", len(nerResponse.Texts), nerResponse)
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
	_ *models.AppState,
	messages []models.Message,
) (Response, error) {
	url := "http://localhost:8080/entities"

	request := make([]RequestRecord, len(messages))
	for i, m := range messages {
		r := RequestRecord{
			UUID:     m.UUID.String(),
			Text:     m.Content,
			Language: "en",
		}
		request[i] = r
	}

	requestBody := Request{Texts: request}
	jsonBody, err := json.Marshal(requestBody)
	if err != nil {
		log.Error("Error marshaling request body:", err)
		return Response{}, err
	}

	resp, err := http.Post(url, "application/json", bytes.NewBuffer(jsonBody))
	if err != nil {
		log.Error("Error making POST request:", err)
		return Response{}, err
	}
	defer resp.Body.Close()

	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		log.Error("Error reading response body:", err)
		return Response{}, err
	}

	var response Response
	err = json.Unmarshal(bodyBytes, &response)
	if err != nil {
		fmt.Println("Error unmarshaling response body:", err)
		return Response{}, err
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

type RequestRecord struct {
	UUID     string `json:"uuid"`
	Text     string `json:"text"`
	Language string `json:"language"`
}

type ResponseRecord struct {
	UUID     string   `json:"uuid"`
	Entities []Entity `json:"entities"`
}

type Request struct {
	Texts []RequestRecord `json:"texts"`
}

type Response struct {
	Texts []ResponseRecord `json:"texts"`
}

type ValidationError struct {
	Loc  []interface{} `json:"loc"`
	Msg  string        `json:"msg"`
	Type string        `json:"type"`
}
