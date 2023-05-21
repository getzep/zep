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
	_ *models.AppState,
	messageEvent *models.MessageEvent,
) error {
	sessionID := messageEvent.SessionID
	sessionMutex := ee.getSessionMutex(sessionID)
	sessionMutex.Lock()
	defer sessionMutex.Unlock()

	nerResponse, err := callNERService(ctx, messageEvent.Messages)
	if err != nil {
		return NewExtractorError("EntityExtractor extract entities call failed", err)
	}

	log.Infof("EntityExtractor received %d entities: %+v", len(nerResponse.Values), nerResponse)
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

func callNERService(_ context.Context, messages []models.Message) (RecordsResponse, error) {
	url := "http://localhost:8080/entities" // Replace with your actual server URL

	request := make([]RecordRequest, len(messages))
	for i, m := range messages {
		r := RecordRequest{
			RecordId: m.UUID.String(),
			Data: RecordDataRequest{
				Text:     m.Content,
				Language: "en",
			},
		}
		request[i] = r
	}

	requestBody := RecordsRequest{Values: request}
	jsonBody, err := json.Marshal(requestBody)
	if err != nil {
		fmt.Println("Error marshaling request body:", err)
		return RecordsResponse{}, err
	}

	resp, err := http.Post(url, "application/json", bytes.NewBuffer(jsonBody))
	if err != nil {
		fmt.Println("Error making POST request:", err)
		return RecordsResponse{}, err
	}
	defer resp.Body.Close()

	bodyBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		fmt.Println("Error reading response body:", err)
		return RecordsResponse{}, err
	}

	var recordsResponse RecordsResponse
	err = json.Unmarshal(bodyBytes, &recordsResponse)
	if err != nil {
		fmt.Println("Error unmarshaling response body:", err)
		return RecordsResponse{}, err
	}

	return recordsResponse, nil
}

type HTTPValidationError struct {
	Detail []ValidationError `json:"detail"`
}

type Message struct {
	Message string `json:"message"`
}

type RecordDataRequest struct {
	Text     string `json:"text"`
	Language string `json:"language"`
}

type RecordDataResponse struct {
	Entities []interface{} `json:"entities"`
}

type RecordRequest struct {
	RecordId string            `json:"recordId"`
	Data     RecordDataRequest `json:"data"`
}

type RecordResponse struct {
	RecordId string             `json:"recordId"`
	Data     RecordDataResponse `json:"data"`
	Errors   []Message          `json:"errors"`
	Warnings []Message          `json:"warnings"`
}

type RecordsRequest struct {
	Values []RecordRequest `json:"values"`
}

type RecordsResponse struct {
	Values []RecordResponse `json:"values"`
}

type ValidationError struct {
	Loc  []interface{} `json:"loc"`
	Msg  string        `json:"msg"`
	Type string        `json:"type"`
}
