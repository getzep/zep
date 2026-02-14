package transform

import (
	"encoding/json"
	"testing"
)

func TestFormatJSON(t *testing.T) {
	tests := []struct {
		name    string
		input   interface{}
		wantErr bool
	}{
		{
			name:    "simple map",
			input:   map[string]string{"key": "value"},
			wantErr: false,
		},
		{
			name:    "struct",
			input:   struct{ Name string }{Name: "test"},
			wantErr: false,
		},
		{
			name:    "nil value",
			input:   nil,
			wantErr: false,
		},
		{
			name:    "slice",
			input:   []int{1, 2, 3},
			wantErr: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := FormatJSON(tt.input)
			if (err != nil) != tt.wantErr {
				t.Errorf("FormatJSON() error = %v, wantErr %v", err, tt.wantErr)
				return
			}
			if !tt.wantErr && result == "" {
				t.Errorf("FormatJSON() returned empty string")
			}
			// Verify it's valid JSON
			if !tt.wantErr && !json.Valid([]byte(result)) {
				t.Errorf("FormatJSON() returned invalid JSON: %s", result)
			}
		})
	}
}

func TestGetOptionalString(t *testing.T) {
	tests := []struct {
		name         string
		params       map[string]interface{}
		key          string
		defaultValue string
		want         string
	}{
		{
			name:         "key exists",
			params:       map[string]interface{}{"key": "value"},
			key:          "key",
			defaultValue: "default",
			want:         "value",
		},
		{
			name:         "key missing",
			params:       map[string]interface{}{},
			key:          "key",
			defaultValue: "default",
			want:         "default",
		},
		{
			name:         "key exists but wrong type",
			params:       map[string]interface{}{"key": 123},
			key:          "key",
			defaultValue: "default",
			want:         "default",
		},
		{
			name:         "nil params",
			params:       nil,
			key:          "key",
			defaultValue: "default",
			want:         "default",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := GetOptionalString(tt.params, tt.key, tt.defaultValue)
			if got != tt.want {
				t.Errorf("GetOptionalString() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestGetOptionalInt(t *testing.T) {
	tests := []struct {
		name         string
		params       map[string]interface{}
		key          string
		defaultValue int
		want         int
	}{
		{
			name:         "key exists as int",
			params:       map[string]interface{}{"key": 42},
			key:          "key",
			defaultValue: 10,
			want:         42,
		},
		{
			name:         "key exists as float64",
			params:       map[string]interface{}{"key": 42.0},
			key:          "key",
			defaultValue: 10,
			want:         42,
		},
		{
			name:         "key missing",
			params:       map[string]interface{}{},
			key:          "key",
			defaultValue: 10,
			want:         10,
		},
		{
			name:         "key exists but wrong type",
			params:       map[string]interface{}{"key": "not a number"},
			key:          "key",
			defaultValue: 10,
			want:         10,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := GetOptionalInt(tt.params, tt.key, tt.defaultValue)
			if got != tt.want {
				t.Errorf("GetOptionalInt() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestGetOptionalFloat(t *testing.T) {
	tests := []struct {
		name         string
		params       map[string]interface{}
		key          string
		defaultValue float64
		want         float64
	}{
		{
			name:         "key exists as float64",
			params:       map[string]interface{}{"key": 3.14},
			key:          "key",
			defaultValue: 1.0,
			want:         3.14,
		},
		{
			name:         "key exists as int",
			params:       map[string]interface{}{"key": 42},
			key:          "key",
			defaultValue: 1.0,
			want:         42.0,
		},
		{
			name:         "key missing",
			params:       map[string]interface{}{},
			key:          "key",
			defaultValue: 1.0,
			want:         1.0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := GetOptionalFloat(tt.params, tt.key, tt.defaultValue)
			if got != tt.want {
				t.Errorf("GetOptionalFloat() = %v, want %v", got, tt.want)
			}
		})
	}
}

func TestValidateRequired(t *testing.T) {
	tests := []struct {
		name    string
		params  map[string]interface{}
		key     string
		wantErr bool
	}{
		{
			name:    "key exists with value",
			params:  map[string]interface{}{"key": "value"},
			key:     "key",
			wantErr: false,
		},
		{
			name:    "key missing",
			params:  map[string]interface{}{},
			key:     "key",
			wantErr: true,
		},
		{
			name:    "key exists but empty string",
			params:  map[string]interface{}{"key": ""},
			key:     "key",
			wantErr: true,
		},
		{
			name:    "key exists but wrong type",
			params:  map[string]interface{}{"key": 123},
			key:     "key",
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := ValidateRequired(tt.params, tt.key)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateRequired() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}
