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

func TestMergeMaps(t *testing.T) {
	map1 := map[string]int{"one": 1, "two": 2}
	map2 := map[string]int{"three": 3, "four": 4}
	map3 := map[string]int{"five": 5, "six": 6}

	expected := map[string]int{
		"one":   1,
		"two":   2,
		"three": 3,
		"four":  4,
		"five":  5,
		"six":   6,
	}

	result := MergeMaps(map1, map2, map3)

	if !reflect.DeepEqual(result, expected) {
		t.Errorf("Expected %v, but got %v", expected, result)
	}
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
	type simpleStruct struct {
		Name  string
		Label string
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

	tests := []struct {
		name     string
		input    interface{}
		expected map[string]interface{}
	}{
		{
			name: "simple struct",
			input: simpleStruct{
				Name:  "Entity1",
				Label: "Label1",
			},
			expected: map[string]interface{}{
				"Name":  "Entity1",
				"Label": "Label1",
			},
		},
		{
			name: "embedded struct",
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
		{
			name: "list of structs",
			input: []Entity{{
				Name:  "Entity1",
				Label: "Label1",
				Matches: []EntityMatch{
					{
						Start: 2,
						End:   3,
						Text:  "Match1",
					},
				},
			}, {
				Name:  "Entity2",
				Label: "Label2",
				Matches: []EntityMatch{
					{
						Start: 2,
						End:   3,
						Text:  "Match2",
					},
					{
						Start: 4,
						End:   6,
						Text:  "Match3",
					},
				},
			}},
			expected: map[string]interface{}{
				"data": []map[string]interface{}{
					{
						"Name":  "Entity1",
						"Label": "Label1",
						"Matches": []map[string]interface{}{
							{
								"Start": 2,
								"End":   3,
								"Text":  "Match1",
							},
						},
					},
					{
						"Name":  "Entity2",
						"Label": "Label2",
						"Matches": []map[string]interface{}{
							{
								"Start": 2,
								"End":   3,
								"Text":  "Match2",
							},
							{
								"Start": 4,
								"End":   6,
								"Text":  "Match3",
							},
						},
					},
				},
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := StructToMap(test.input)
			// We should do this better
			gotString := fmt.Sprint(got)
			expectedString := fmt.Sprint(test.expected)
			assert.True(t, gotString == expectedString)
		})
	}
}
