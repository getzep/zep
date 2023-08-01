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

type DocumentSearchPayload struct {
	CollectionName string                 `json:"collection_name"`
	Text           string                 `json:"text,omitempty"`
	Embedding      []float32              `json:"embedding,omitempty"`
	Metadata       map[string]interface{} `json:"metadata,omitempty"`
}

type DocumentSearchResult struct {
	*DocumentResponse
	Score float64 `json:"score"`
}

type DocumentSearchResultPage struct {
	Results     []DocumentSearchResult `json:"results"`
	QueryVector []float32              `json:"query_vector"`
	ResultCount int                    `json:"result_count"`
	TotalPages  int                    `json:"total_pages"`
	CurrentPage int                    `json:"current_page"`
}
