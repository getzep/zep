package apidata

import (
	"time"

	"github.com/google/uuid"

	"github.com/getzep/zep/models"
)

func SessionListTransformer(sessions []*models.Session) []Session {
	transformedSessions := make([]Session, len(sessions))
	for i, session := range sessions {
		transformedSessions[i] = SessionTransformer(session)
	}
	return transformedSessions
}

func SessionSearchResultListTransformer(result []models.SessionSearchResult) []SessionSearchResult {
	transformedResults := make([]SessionSearchResult, len(result))
	for i, r := range result {
		transformedResults[i] = SessionSearchResultTransformer(r)
	}

	return transformedResults
}

func SessionTransformer(session *models.Session) Session {
	s := Session{
		SessionCommon: SessionCommon{
			UUID:      session.UUID,
			ID:        session.ID,
			CreatedAt: session.CreatedAt,
			UpdatedAt: session.UpdatedAt,
			DeletedAt: session.DeletedAt,
			EndedAt:   session.EndedAt,
			SessionID: session.SessionID,
			Metadata:  session.Metadata,
			UserID:    session.UserID,
		},
	}

	transformSession(session, &s)

	return s
}

type SessionCommon struct {
	UUID      uuid.UUID      `json:"uuid"`
	ID        int64          `json:"id"`
	CreatedAt time.Time      `json:"created_at"`
	UpdatedAt time.Time      `json:"updated_at"`
	DeletedAt *time.Time     `json:"deleted_at"`
	EndedAt   *time.Time     `json:"ended_at"`
	SessionID string         `json:"session_id"`
	Metadata  map[string]any `json:"metadata"`
	// Must be a pointer to allow for null values
	UserID      *string   `json:"user_id"`
	ProjectUUID uuid.UUID `json:"project_uuid"`
}

type SessionSearchResultCommon struct {
	Fact *Fact `json:"fact"`
}

type SessionSearchResponse struct {
	Results []SessionSearchResult `json:"results"`
}

type SessionListResponse struct {
	Sessions   []Session `json:"sessions"`
	TotalCount int       `json:"total_count"`
	RowCount   int       `json:"response_count"`
}

type CreateSessionRequestCommon struct {
	// The unique identifier of the session.
	SessionID string `json:"session_id" validate:"required"`
	// The unique identifier of the user associated with the session
	UserID *string `json:"user_id"`
	// The metadata associated with the session.
	Metadata map[string]any `json:"metadata"`
}
