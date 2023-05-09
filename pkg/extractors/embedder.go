package extractors

import (
	"context"
	"fmt"
	"strings"

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

	if len(unembeddedMessages) == 0 {
		return nil
	}

	texts := embeddingsToTextSlice(unembeddedMessages, false)

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

// embeddingsToTextSlice converts a slice of Embeddings to a slice of strings.
// If enrich is true, the text slice will include the prior and subsequent
// messages text to the slice item.
func embeddingsToTextSlice(messages []models.Embeddings, enrich bool) []string {
	texts := make([]string, len(messages))
	for i, r := range messages {
		if !enrich {
			texts[i] = r.Text
			continue
		}

		var parts []string

		if i > 0 {
			parts = append(parts, messages[i-1].Text)
		}

		parts = append(parts, r.Text)

		if i < len(messages)-1 {
			parts = append(parts, messages[i+1].Text)
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
