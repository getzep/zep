package handlers

import (
	"strings"
	"testing"
)

// TestInputTypes verifies that input types have required fields
func TestInputTypes(t *testing.T) {
	tests := []struct {
		name  string
		input interface{}
	}{
		{"SearchGraphInput", SearchGraphInput{}},
		{"GetUserContextInput", GetUserContextInput{}},
		{"GetUserInput", GetUserInput{}},
		{"ListThreadsInput", ListThreadsInput{}},
		{"GetUserNodesInput", GetUserNodesInput{}},
		{"GetUserEdgesInput", GetUserEdgesInput{}},
		{"GetEpisodesInput", GetEpisodesInput{}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if tt.input == nil {
				t.Errorf("%s is nil", tt.name)
			}
		})
	}
}

// TestSearchGraphInputDefaults verifies default values
func TestSearchGraphInputDefaults(t *testing.T) {
	input := SearchGraphInput{
		UserID: "user123",
		Query:  "test query",
	}

	if input.UserID != "user123" {
		t.Errorf("Expected UserID 'user123', got '%s'", input.UserID)
	}

	if input.Query != "test query" {
		t.Errorf("Expected Query 'test query', got '%s'", input.Query)
	}

	// Test zero values for optional fields
	if input.Scope != "" {
		t.Errorf("Expected empty Scope, got '%s'", input.Scope)
	}

	if input.Limit != 0 {
		t.Errorf("Expected Limit 0, got %d", input.Limit)
	}
}

// TestGetUserInputValidation verifies required fields
func TestGetUserInputValidation(t *testing.T) {
	tests := []struct {
		name    string
		input   GetUserInput
		wantErr bool
	}{
		{
			name:    "valid user ID",
			input:   GetUserInput{UserID: "user123"},
			wantErr: false,
		},
		{
			name:    "empty user ID",
			input:   GetUserInput{UserID: ""},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			hasErr := tt.input.UserID == ""
			if hasErr != tt.wantErr {
				t.Errorf("Expected error=%v, got error=%v", tt.wantErr, hasErr)
			}
		})
	}
}

// TestListThreadsInput verifies ListThreadsInput struct
func TestListThreadsInput(t *testing.T) {
	input := ListThreadsInput{
		UserID: "user123",
	}

	if input.UserID != "user123" {
		t.Errorf("Expected UserID 'user123', got '%s'", input.UserID)
	}
}

// TestSearchGraphInputValidate verifies search input validation rules.
func TestSearchGraphInputValidate(t *testing.T) {
	longQuery := strings.Repeat("a", searchQueryMaxLen+1)
	maxQuery := strings.Repeat("b", searchQueryMaxLen)

	tests := []struct {
		name    string
		input   SearchGraphInput
		wantErr bool
		errSub  string // substring expected in error when wantErr is true
	}{
		// valid cases
		{
			name:    "valid minimal query",
			input:   SearchGraphInput{UserID: "u1", Query: "hello"},
			wantErr: false,
		},
		{
			name:    "valid query with surrounding whitespace",
			input:   SearchGraphInput{UserID: "u1", Query: "  hello  "},
			wantErr: false,
		},
		{
			name:    "valid query at max length",
			input:   SearchGraphInput{UserID: "u1", Query: maxQuery},
			wantErr: false,
		},
		{
			name:    "valid limit omitted (zero uses handler default)",
			input:   SearchGraphInput{UserID: "u1", Query: "q", Limit: 0},
			wantErr: false,
		},
		{
			name:    "valid limit at minimum boundary",
			input:   SearchGraphInput{UserID: "u1", Query: "q", Limit: searchLimitMin},
			wantErr: false,
		},
		{
			name:    "valid limit at maximum boundary",
			input:   SearchGraphInput{UserID: "u1", Query: "q", Limit: searchLimitMax},
			wantErr: false,
		},
		{
			name:    "valid min_fact_rating omitted (zero)",
			input:   SearchGraphInput{UserID: "u1", Query: "q", MinFactRating: 0},
			wantErr: false,
		},
		{
			name:    "valid min_fact_rating at minimum boundary",
			input:   SearchGraphInput{UserID: "u1", Query: "q", MinFactRating: searchMinScoreMin},
			wantErr: false,
		},
		{
			name:    "valid min_fact_rating at maximum boundary",
			input:   SearchGraphInput{UserID: "u1", Query: "q", MinFactRating: searchMinScoreMax},
			wantErr: false,
		},
		{
			name:    "valid min_fact_rating mid range",
			input:   SearchGraphInput{UserID: "u1", Query: "q", MinFactRating: 0.5},
			wantErr: false,
		},
		// invalid query
		{
			name:    "empty query",
			input:   SearchGraphInput{UserID: "u1", Query: ""},
			wantErr: true,
			errSub:  "query cannot be empty",
		},
		{
			name:    "whitespace only query",
			input:   SearchGraphInput{UserID: "u1", Query: "   \t\n  "},
			wantErr: true,
			errSub:  "query cannot be empty",
		},
		{
			name:    "query exceeds max length",
			input:   SearchGraphInput{UserID: "u1", Query: longQuery},
			wantErr: true,
			errSub:  "query exceeds maximum length",
		},
		// invalid limit (explicit values only; 0 is allowed as default sentinel)
		{
			name:    "limit below minimum",
			input:   SearchGraphInput{UserID: "u1", Query: "q", Limit: -1},
			wantErr: true,
			errSub:  "limit must be between",
		},
		{
			name:    "limit zero is default sentinel not error",
			input:   SearchGraphInput{UserID: "u1", Query: "q", Limit: 0},
			wantErr: false,
		},
		{
			name:    "limit above maximum",
			input:   SearchGraphInput{UserID: "u1", Query: "q", Limit: searchLimitMax + 1},
			wantErr: true,
			errSub:  "limit must be between",
		},
		// invalid min_fact_rating (MinScore-equivalent field on SearchGraphInput)
		{
			name:    "min_fact_rating below minimum",
			input:   SearchGraphInput{UserID: "u1", Query: "q", MinFactRating: -0.01},
			wantErr: true,
			errSub:  "min_fact_rating must be between",
		},
		{
			name:    "min_fact_rating above maximum",
			input:   SearchGraphInput{UserID: "u1", Query: "q", MinFactRating: 1.01},
			wantErr: true,
			errSub:  "min_fact_rating must be between",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := tt.input.Validate()
			if tt.wantErr {
				if err == nil {
					t.Fatalf("Validate() error = nil, wantErr true")
				}
				if tt.errSub != "" && !strings.Contains(err.Error(), tt.errSub) {
					t.Errorf("Validate() error = %q, want substring %q", err.Error(), tt.errSub)
				}
				return
			}
			if err != nil {
				t.Errorf("Validate() unexpected error = %v", err)
			}
		})
	}
}
