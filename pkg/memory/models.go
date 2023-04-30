package memory

type SearchPayload struct {
	Text string `json:"text"`
}

type MemoryMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type MemoryMessagesAndContext struct {
	Messages []MemoryMessage `json:"messages"`
	Summary  string          `json:"summary,omitempty"`
}

type MemoryResponse struct {
	Messages []MemoryMessage `json:"messages"`
	Summary  string          `json:"summary,omitempty"`
	Tokens   int64           `json:"tokens"`
}

type AckResponse struct {
	Status string `json:"status"`
}

type RedisearchResult struct {
	Role    string  `json:"role"`
	Content string  `json:"content"`
	Dist    float64 `json:"dist"`
}
