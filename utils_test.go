package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

type TestObject struct {
	Name string `json:"name"`
	Age  int    `json:"age"`
}

func TestRespondJSON(t *testing.T) {
	// Test case 1 - Valid JSON object
	data1 := TestObject{Name: "Alice", Age: 30}
	rr1 := httptest.NewRecorder()
	respondJSON(rr1, data1, http.StatusOK)

	expectedHeaders := "application/json"
	contentType1 := rr1.Header().Get("Content-Type")
	if contentType1 != expectedHeaders {
		t.Errorf("Expected content type %v, but got %v", expectedHeaders, contentType1)
	}

	if rr1.Code != http.StatusOK {
		t.Errorf("Expected status code %v, but got %v", http.StatusOK, rr1.Code)
	}

	// Test case 2 - Invalid JSON object: Unsupported data type
	data2 := make(chan int)
	rr2 := httptest.NewRecorder()
	respondJSON(rr2, data2, http.StatusOK)
	errorResponse, _ := json.Marshal(
		parseErrorResponse(errors.New("json: unsupported type: chan int")),
	)

	if rr2.Code != http.StatusInternalServerError {
		t.Errorf("Expected status code %v, but got %v", http.StatusInternalServerError, rr2.Code)
	}

	errorResponseReceived := rr2.Body.Bytes()
	if string(errorResponse) != string(errorResponseReceived) {
		t.Errorf(
			"Expected error response %v, but got %v",
			string(errorResponse),
			string(errorResponseReceived),
		)
	}
}

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
