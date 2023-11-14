package search

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestExtractQuestions(t *testing.T) {
	m := &MultiQuestionSummaryRetriever{}

	testCases := []struct {
		xmlData  string
		expected []string
	}{
		{
			xmlData:  "<questions>Question 1</questions>",
			expected: []string{"Question 1"},
		},
		{
			xmlData:  "<questions>Question 1\nQuestion 2</questions>",
			expected: []string{"Question 1", "Question 2"},
		},
	}

	for _, tc := range testCases {
		result := m.extractQuestions(tc.xmlData)
		assert.Equal(t, tc.expected, result)
	}
}
