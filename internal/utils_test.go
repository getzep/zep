package internal

import (
	"errors"
	"fmt"
	"reflect"
	"testing"

	"github.com/stretchr/testify/assert"
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

func TestStructToMap(t *testing.T) {
	tests := []struct {
		name     string
		input    Entity
		expected map[string]interface{}
	}{
		{
			name: "Test Entity 1",
			input: Entity{
				Name:  "Entity1",
				Label: "Label1",
				Matches: []EntityMatch{
					{
						Start: 0,
						End:   1,
						Text:  "Match1",
					},
				},
			},
			expected: map[string]interface{}{
				"Name":  "Entity1",
				"Label": "Label1",
				"Matches": []map[string]interface{}{
					{
						"Start": 0,
						"End":   1,
						"Text":  "Match1",
					},
				},
			},
		},
		{
			name: "Test Entity 2",
			input: Entity{
				Name:  "Entity2",
				Label: "Label2",
				Matches: []EntityMatch{
					{
						Start: 2,
						End:   3,
						Text:  "Match2",
					},
				},
			},
			expected: map[string]interface{}{
				"Name":  "Entity2",
				"Label": "Label2",
				"Matches": []map[string]interface{}{
					{
						"Start": 2,
						"End":   3,
						"Text":  "Match2",
					},
				},
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := StructToMap(test.input)
			// We should do this better
			assert.True(t, fmt.Sprint(got) == fmt.Sprint(test.expected))
		})
	}
}

type EntityMatch struct {
	Start int    `json:"start"`
	End   int    `json:"end"`
	Text  string `json:"text"`
}

type Entity struct {
	Name    string        `json:"name"`
	Label   string        `json:"label"`
	Matches []EntityMatch `json:"matches"`
}
