package models

type SessionSearchQueryCommon struct {
	// The search text.
	Text string `json:"text"`
	// User ID used to determine which sessions to search. Required on Community Edition.
	UserID string `json:"user_id,omitempty"`

	// the session ids to search
	SessionIDs []string `json:"session_ids,omitempty"`
}

type SessionSearchResultCommon struct {
	Fact      *Fact     `json:"fact"`
	Embedding []float32 `json:"-" swaggerignore:"true"`
}

type SessionSearchRequest struct {
	Query *SessionSearchQuery `json:"query"`
}

type SessionSearchResponse struct {
	Results []SessionSearchResult `json:"results"`
}
