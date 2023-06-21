package models

import "github.com/google/uuid"

type Intent struct {
	UUID      uuid.UUID `json:"uuid,omitempty"`
	Name      string    `json:"name,omitempty"`
	Documents []string  `json:"documents"`
}

type IntentCollection struct {
	UUID    uuid.UUID `json:"uuid,omitempty"`
	Name    string    `json:"name,omitempty"`
	Intents []Intent  `json:"intents"`
}

type IntentResponse struct {
	Intent string `json:"intent"`
}

type IntentPromptTemplateData struct {
	Input string
}
