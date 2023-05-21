package extractors

import (
	"context"
	"testing"

	"github.com/google/uuid"

	"github.com/stretchr/testify/assert"

	"github.com/getzep/zep/pkg/models"
)

func TestEntityExtractor_Extract(t *testing.T) {
	ctx := context.Background()
	entityExtractor := NewEntityExtractor()

	messageEvent := &models.MessageEvent{
		SessionID: "test",
		Messages: []models.Message{
			{
				Content: "HIROSHIMA, Japan — President Volodymyr Zelensky of Ukraine rejected Russia’s claim on Sunday to have captured the eastern city of Bakhmut after nearly a year of fighting, as President Biden reaffirmed that Western allies “will not waver” in their support of Kyiv.",
				UUID:    uuid.New(),
			},
		},
	}

	err := entityExtractor.Extract(ctx, nil, messageEvent)
	assert.NoError(t, err)
}
