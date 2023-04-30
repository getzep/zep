package memory

type SearchPayload struct {
	Text string `json:"text"`
}

type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type MessagesAndSummary struct {
	Messages []Message `json:"messages"`
	Summary  string    `json:"summary,omitempty"`
}

type Response struct {
	Messages []Message `json:"messages"`
	Summary  string    `json:"summary,omitempty"`
	Tokens   int64     `json:"tokens"`
}

type AckResponse struct {
	Status string `json:"status"`
}

type RedisearchResult struct {
	Role    string  `json:"role"`
	Content string  `json:"content"`
	Dist    float64 `json:"dist"`
}
