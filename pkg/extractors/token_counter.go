package extractors

import (
	"context"
	"fmt"

	"github.com/getzep/zep/pkg/models"
)

// Force compiler to validate that our Extractor implements the Extractor interface.
var _ models.Extractor = &TokenCountExtractor{}

type TokenCountExtractor struct {
	BaseExtractor
	appState *models.AppState
}

func (ee *TokenCountExtractor) Extract(
	ctx context.Context,
	appState *models.AppState,
	messageEvents *models.MessageEvent,
) error {
	sessionID := messageEvents.SessionID
	sessionMutex := ee.getSessionMutex(sessionID)
	sessionMutex.Lock()
	defer sessionMutex.Unlock()

	ee.appState = appState

	countResult, err := ee.updateTokenCounts(messageEvents.Messages)
	if err != nil {
		return NewExtractorError("TokenCountExtractor failed to get token count", err)
	}

	if len(countResult) == 0 {
		return nil
	}

	err = appState.MemoryStore.PutMemory(
		ctx,
		appState,
		messageEvents.SessionID,
		&models.Memory{Messages: countResult},
		true,
	)
	if err != nil {
		return NewExtractorError("TokenCountExtractor update messages failed", err)
	}
	return nil
}

func (ee *TokenCountExtractor) updateTokenCounts(
	messages []models.Message,
) ([]models.Message, error) {
	var countResult []models.Message //nolint:prealloc

	for _, m := range messages {
		if m.TokenCount != 0 {
			continue
		}
		t, err := ee.appState.LLMClient.GetTokenCount(fmt.Sprintf("%s: %s", m.Role, m.Content))
		if err != nil {
			return nil, err
		}
		m.TokenCount = t
		countResult = append(countResult, m)
	}
	return countResult, nil
}

func (ee *TokenCountExtractor) Notify(
	ctx context.Context,
	appState *models.AppState,
	messageEvents *models.MessageEvent,
) error {
	if messageEvents == nil {
		return NewExtractorError(
			"TokenCountExtractor message events is nil at Notify",
			nil,
		)
	}
	log.Debugf("TokenCountExtractor extract: %d messages", len(messageEvents.Messages))
	go func() {
		err := ee.Extract(ctx, appState, messageEvents)
		if err != nil {
			log.Error(fmt.Sprintf("TokenCountExtractor extract failed: %v", err))
		}
	}()
	return nil
}

func NewTokenCountExtractor() *TokenCountExtractor {
	return &TokenCountExtractor{}
}
