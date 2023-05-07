package models

type SearchResult struct {
	Message *Message               `json:"message"`
	Summary *Summary               `json:"summary"`        // reserved for future use
	Meta    map[string]interface{} `json:"meta,omitempty"` // reserved for future use
	Dist    float64                `json:"dist"`
}

type SearchPayload struct {
	Text string                 `json:"text"`
	Meta map[string]interface{} `json:"meta,omitempty"` // reserved for future use
}
