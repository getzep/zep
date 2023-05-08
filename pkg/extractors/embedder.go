package extractors

import (
	"context"
	"fmt"

	"github.com/danielchalef/zep/pkg/llms"
	"github.com/danielchalef/zep/pkg/models"
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
	sessionID := messageEvent.SessionID
	sessionMutex := ee.getSessionMutex(sessionID)
	sessionMutex.Lock()
	defer sessionMutex.Unlock()

	unembeddedMessages, err := appState.MemoryStore.GetMessageVectors(
		ctx,
		appState,
		messageEvent.SessionID,
		false,
	)
	if err != nil {
		return NewExtractorError("EmbeddingExtractor get message vectors failed", err)
	}

	texts := make([]string, len(unembeddedMessages))
	for i, r := range unembeddedMessages {
		texts[i] = r.Text
	}

	embeddings, err := llms.EmbedMessages(ctx, appState, texts)
	if err != nil {
		return NewExtractorError("EmbeddingExtractor embed messages failed", err)
	}

	embeddingRecords := make([]models.Embeddings, len(unembeddedMessages))
	for i, r := range unembeddedMessages {
		embeddingRecords[i] = models.Embeddings{
			TextUUID:  r.TextUUID,
			Embedding: (*embeddings)[i].Embedding,
		}
	}
	err = appState.MemoryStore.PutMessageVectors(
		ctx,
		appState,
		messageEvent.SessionID,
		embeddingRecords,
		true,
	)
	if err != nil {
		return NewExtractorError("EmbeddingExtractor put message vectors failed", err)
	}
	return nil
}

func (ee *EmbeddingExtractor) Notify(
	ctx context.Context,
	appState *models.AppState,
	messageEvents *models.MessageEvent,
) error {
	log.Debugf("EmbeddingExtractor notify: %v", messageEvents)
	if messageEvents == nil {
		return NewExtractorError(
			"EmbeddingExtractor message events is nil at Notify",
			nil,
		)
	}
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
