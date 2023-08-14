package extractors

import (
	"context"
	"fmt"
	"strings"

	"github.com/getzep/zep/pkg/llms"
	"github.com/getzep/zep/pkg/models"
)

// Force compiler to validate that Extractor implements the MemoryStore interface.
var _ models.Extractor = &EmbeddingExtractor{}

type EmbeddingExtractor struct {
	BaseExtractor
}

func (ee *EmbeddingExtractor) Extract(
	ctx context.Context,
	appState *models.AppState,
	messageEvent *models.MessageEvent,
) error {
	messageType := "message"
	sessionID := messageEvent.SessionID
	sessionMutex := ee.getSessionMutex(sessionID)
	sessionMutex.Lock()
	defer sessionMutex.Unlock()

	texts := messageToStringSlice(messageEvent.Messages, false)

	model, err := llms.GetEmbeddingModel(appState, messageType)
	if err != nil {
		return NewExtractorError("EmbeddingExtractor get message embedding model failed", err)
	}

	embeddings, err := llms.EmbedTexts(ctx, appState, model, messageType, texts)
	if err != nil {
		return NewExtractorError("EmbeddingExtractor embed messages failed", err)
	}

	embeddingRecords := make([]models.MessageEmbedding, len(messageEvent.Messages))
	for i, r := range messageEvent.Messages {
		embeddingRecords[i] = models.MessageEmbedding{
			TextUUID:  r.UUID,
			Embedding: embeddings[i],
		}
	}
	err = appState.MemoryStore.PutMessageVectors(
		ctx,
		appState,
		messageEvent.SessionID,
		embeddingRecords,
	)
	if err != nil {
		return NewExtractorError("EmbeddingExtractor put message vectors failed", err)
	}
	return nil
}

// messageToStringSlice converts a slice of MessageEmbedding to a slice of strings.
// If enrich is true, the text slice will include the prior and subsequent
// messages text to the slice item.
func messageToStringSlice(messages []models.Message, enrich bool) []string {
	texts := make([]string, len(messages))
	for i, r := range messages {
		if !enrich {
			texts[i] = r.Content
			continue
		}

		var parts []string

		if i > 0 {
			parts = append(parts, messages[i-1].Content)
		}

		parts = append(parts, r.Content)

		if i < len(messages)-1 {
			parts = append(parts, messages[i+1].Content)
		}

		texts[i] = strings.Join(parts, " ")
	}
	return texts
}

func (ee *EmbeddingExtractor) Notify(
	ctx context.Context,
	appState *models.AppState,
	messageEvents *models.MessageEvent,
) error {
	if messageEvents == nil {
		return NewExtractorError(
			"EmbeddingExtractor message events is nil at Notify",
			nil,
		)
	}
	log.Debugf("EmbeddingExtractor notify: %d messages", len(messageEvents.Messages))
	go func() {
		err := ee.Extract(ctx, appState, messageEvents)
		if err != nil {
			log.Error(fmt.Sprintf("EmbeddingExtractor extract failed: %v", err))
		}
	}()
	return nil
}

func NewEmbeddingExtractor() *EmbeddingExtractor {
	return &EmbeddingExtractor{}
}
