package internal

import (
	"errors"
	"reflect"
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
			expectedErr:    errors.New("template: prompt:1: unexpected \".\" in operand"),
		},
		{
			name:           "Invalid data property",
			promptTemplate: "Hello, my name is {{.InvalidProperty}}.",
			data:           testData{Name: "John"},
			expected:       "",
			expectedErr: errors.New(
				"template: prompt:1:20: executing \"prompt\" at <.InvalidProperty>: can't evaluate field InvalidProperty in type internal.testData",
			),
		},
	}
	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			result, err := ParsePrompt(tc.promptTemplate, tc.data)
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

func TestReverseSlice(t *testing.T) {
	testCases := []struct {
		name     string
		input    []string
		expected []string
	}{
		{
			name:     "Empty slice",
			input:    []string{},
			expected: []string{},
		},
		{
			name:     "Slice with one element",
			input:    []string{"a"},
			expected: []string{"a"},
		},
		{
			name:     "Slice with even number of elements",
			input:    []string{"a", "b", "c", "d"},
			expected: []string{"d", "c", "b", "a"},
		},
		{
			name:     "Slice with odd number of elements",
			input:    []string{"a", "b", "c", "d", "e"},
			expected: []string{"e", "d", "c", "b", "a"},
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			ReverseSlice(tc.input)
			if !reflect.DeepEqual(tc.input, tc.expected) {
				t.Errorf("ReverseSlice(%v) = %v; want %v", tc.input, tc.input, tc.expected)
			}
		})
	}
}
