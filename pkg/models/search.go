package models

type MemorySearchResult struct {
	Message  *Message               `json:"message"`
	Summary  *Summary               `json:"summary"` // reserved for future use
	Metadata map[string]interface{} `json:"metadata,omitempty"`
	Dist     float64                `json:"dist"`
}

type MemorySearchPayload struct {
	Text     string                 `json:"text"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

type SessionSearchPayload struct {
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}
