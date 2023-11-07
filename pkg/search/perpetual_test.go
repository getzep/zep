package search

import (
	"testing"

	"github.com/getzep/zep/pkg/models"
	"github.com/stretchr/testify/assert"
)

func TestGenerateHistoryString(t *testing.T) {
	m := &MultiQuestionRetriever{
		HistoryMessages: []models.Message{
			{
				Role:    "user",
				Content: "Hello, how are you?",
			},
			{
				Role:    "bot",
				Content: "I'm fine, thank you!",
			},
		},
	}

	expected := "user: Hello, how are you?\nbot: I'm fine, thank you!\n"
	result := m.generateHistoryString()
	assert.Equal(t, expected, result)
}

func TestExtractQuestions(t *testing.T) {
	m := &MultiQuestionRetriever{}

	xmlData := "<questions>Question 1\nQuestion 2</questions>"
	expected := []string{"Question 1", "Question 2"}
	result := m.extractQuestions(xmlData)

	assert.Equal(t, expected, result)
}
