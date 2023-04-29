package main

import (
	"errors"
	"testing"
)

type testData struct {
	Name string
}

func TestParsePrompt(t *testing.T) {
	testCases := []struct {
		name           string
		promptTemplate string
		data           interface{}
		expected       string
		expectedErr    error
	}{
		{
			name:           "Valid template and data",
			promptTemplate: "Hello, my name is {{.Name}}.",
			data:           testData{Name: "John"},
			expected:       "Hello, my name is John.",
			expectedErr:    nil,
		},
		{
			name:           "Invalid template",
			promptTemplate: "Hello, my name is {{.Name.",
			data:           testData{Name: "John"},
			expected:       "",
			expectedErr:    errors.New("template: prompt:1: unexpected \"{\" in command"),
		},
		{
			name:           "Invalid data property",
			promptTemplate: "Hello, my name is {{.InvalidProperty}}.",
			data:           testData{Name: "John"},
			expected:       "",
		},
	}
	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			result, err := parsePrompt(tc.promptTemplate, tc.data)
			if result != tc.expected {
				t.Errorf("Expected: %s, Got: %s", tc.expected, result)
			}
			if (err == nil) != (tc.expectedErr == nil) ||
				(err != nil && err.Error() != tc.expectedErr.Error()) {
				t.Errorf("Expected error: %v, Got error: %v", tc.expectedErr, err)
			}
		})
	}
}
