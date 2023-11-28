package webhandlers

import (
	"context"
	"errors"
	"net/http"

	"github.com/getzep/zep/pkg/web"

	"github.com/getzep/zep/pkg/models"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"github.com/uptrace/bun"
)

func NewSessionDetails(
	memoryStore models.MemoryStore[*bun.DB],
	r *http.Request,
	sessionID string,
) *SessionDetails {
	t := web.NewTable("messages-table", nil)
	t.ParseQueryParams(r)
	return &SessionDetails{
		MemoryStore: memoryStore,
		SessionID:   sessionID,
		Table:       t,
	}
}

type SessionDetails struct {
	MemoryStore models.MemoryStore[*bun.DB]
	SessionID   string
	Session     *models.Session
	Messages    []models.Message
	*web.Table
}

func mergeMessagesSummaries(
	messages []models.Message,
	summaries []models.Summary,
) []models.Message {
	// Create a map to hold the summaries with the MessagePointUUID as the key
	summariesMap := make(map[uuid.UUID]models.Summary)
	for _, summary := range summaries {
		summariesMap[summary.SummaryPointUUID] = summary
	}

	// Iterate over the messages and insert the summary immediately after the message with UUID = summaries MessagePointUUID
	var merged []models.Message
	for _, message := range messages {
		merged = append(merged, message)
		if summary, ok := summariesMap[message.UUID]; ok {
			s := models.Message{
				Role:       "summarizer",
				CreatedAt:  summary.CreatedAt,
				Content:    summary.Content,
				Metadata:   summary.Metadata,
				TokenCount: summary.TokenCount,
			}
			merged = append(merged, s)
			// Remove the summary from the map to prevent it from being added again
			delete(summariesMap, message.UUID)
		}
	}

	return merged
}

func (m *SessionDetails) Get(ctx context.Context, _ *models.AppState) error {
	messages, err := m.MemoryStore.GetMessageList(
		ctx,
		m.SessionID,
		m.CurrentPage,
		m.PageSize,
	)
	if err != nil {
		return err
	}

	if messages == nil || len(messages.Messages) == 0 {
		return nil
	}

	// GetSummaryList returns a list of summaries for the given session. It can be
	// paginated, but we are not using pagination to ensure that we retrieve all summaries for
	// the messages we're displaying.
	// Hacky solution: set the page offset to 0 and the limit to a large number
	summaries, err := m.MemoryStore.GetSummaryList(
		ctx,
		m.SessionID,
		0,
		9999,
	)
	if err != nil {
		return err
	}
	if len(summaries.Summaries) > 0 {
		messages.Messages = mergeMessagesSummaries(messages.Messages, summaries.Summaries)
	}
	m.Messages = messages.Messages
	m.TotalCount = messages.TotalCount
	m.RowCount = messages.RowCount
	m.PageCount = m.GetPageCount()
	m.Offset = m.GetOffset()

	return nil
}

func GetSessionDetailsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionID")
		if sessionID == "" {
			handleError(w, errors.New("user id not provided"), "user id not provided")
			return
		}

		userID := chi.URLParam(r, "userID")

		// Get Messages
		sessionDetails := NewSessionDetails(
			appState.MemoryStore,
			r,
			sessionID,
		)

		err := sessionDetails.Get(r.Context(), appState)
		if err != nil {
			handleError(w, err, "failed to get message list")
			return
		}

		// Get Session Details
		session, err := appState.MemoryStore.GetSession(r.Context(), sessionID)
		if err != nil {
			handleError(w, err, "failed to get session")
			return
		}
		sessionDetails.Session = session

		var breadCrumbs []web.BreadCrumb
		if len(userID) == 0 {
			breadCrumbs = []web.BreadCrumb{
				{
					Title: "Sessions",
					Path:  "/admin/sessions",
				},
				{
					Title: sessionID,
					Path:  "/admin/sessions/" + sessionID,
				},
			}
		} else {
			breadCrumbs = []web.BreadCrumb{
				{
					Title: "Users",
					Path:  "/admin/users",
				},
				{
					Title: userID,
					Path:  "/admin/users/" + userID,
				},
				{
					Title: sessionID,
					Path:  "/admin/users/" + userID + "/sessions/" + sessionID,
				},
			}
		}

		var path string
		if len(userID) == 0 {
			path = sessionDetails.GetTablePath("/admin/sessions/" + sessionID)
		} else {
			path = "/admin/users/" + userID + "/session/" + sessionID
			path = sessionDetails.GetTablePath(path)
		}

		page := web.NewPage(
			sessionID,
			"View session information and chat history",
			path,
			[]string{
				"templates/pages/session_details.html",
				"templates/components/content/*.html",
				"templates/components/chat_history.html",
			},
			breadCrumbs,
			sessionDetails,
		)

		page.Render(w, r)
	}
}

func DeleteSessionHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sessionID := chi.URLParam(r, "sessionID")
		if sessionID == "" {
			handleError(w, errors.New("session id not provided"), "session id not provided")
			return
		}

		err := appState.MemoryStore.DeleteSession(r.Context(), sessionID)
		if err != nil {
			handleError(w, err, "failed to delete session")
			return
		}

		userID := chi.URLParam(r, "userID")
		if len(userID) == 0 {
			GetSessionListHandler(appState)(w, r)
		} else {
			GetUserDetailsHandler(appState)(w, r)
		}

	}
}
