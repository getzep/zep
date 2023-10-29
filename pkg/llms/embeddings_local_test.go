package llms

import (
	"context"
	"fmt"
	"testing"
	"time"

	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/testutils"
	"github.com/stretchr/testify/assert"
)

func TestEmbedLocal(t *testing.T) {
	cfg := testutils.NewTestConfig()
	appState := &models.AppState{Config: cfg}
	vectorLength := 384
	messageContents := []string{"Text 1", "Text 2"}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	documentTypes := []string{"message", "summary", "document"}

	for _, documentType := range documentTypes {
		t.Run(fmt.Sprintf("documentType=%s", documentType), func(t *testing.T) {
			embeddings, err := embedTextsLocal(ctx, appState, documentType, messageContents)
			assert.NoError(t, err)
			assert.NotNil(t, embeddings)
			assert.Len(t, embeddings, 2)
			for _, embedding := range embeddings {
				assert.Len(t, embedding, vectorLength)
			}
		})
	}
}
