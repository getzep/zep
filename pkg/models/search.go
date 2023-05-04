package models

type SearchResult struct {
	Role    string         `json:"role"`
	Content string         `json:"content"`
	Meta    map[string]any `json:"meta,omitempty"`
	Dist    float64        `json:"dist"`
}

type SearchPayload struct {
	Text string         `json:"text"`
	Meta map[string]any `json:"meta,omitempty"`
}
