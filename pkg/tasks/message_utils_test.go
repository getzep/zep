package tasks

import (
	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
	"testing"
)

func TestDropEmptyMessages(t *testing.T) {
	messages := []models.Message{
		{Content: "Hello"},
		{Content: " "},
		{Content: "\n"},
		{Content: "World"},
		{Content: ""},
	}

	result := dropEmptyMessages(messages)

	assert.Equal(t, 2, len(result), "Expected 2 messages")
	assert.Equal(t, "Hello", result[0].Content, "Expected first message to be 'Hello'")
	assert.Equal(t, "World", result[1].Content, "Expected second message to be 'World'")
}
