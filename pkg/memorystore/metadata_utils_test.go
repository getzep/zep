package memorystore

import (
	"reflect"
	"testing"
)

func TestStoreMetadataByPath(t *testing.T) {
	tests := []struct {
		name        string
		value       map[string]interface{}
		keyPath     []string
		metadata    interface{}
		expected    map[string]interface{}
		expectedErr string
	}{
		{
			name:     "no key path",
			value:    map[string]interface{}{"a": 1},
			keyPath:  []string{},
			metadata: map[string]interface{}{"b": 2},
			expected: map[string]interface{}{"a": 1, "b": 2},
		},
		{
			name:     "key path is empty string",
			value:    map[string]interface{}{"a": 1},
			keyPath:  []string{""},
			metadata: map[string]interface{}{"b": 2},
			expected: map[string]interface{}{"a": 1, "b": 2},
		},
		{
			name:     "simple key path",
			value:    map[string]interface{}{"a": 1},
			keyPath:  []string{"b"},
			metadata: map[string]interface{}{"c": 2},
			expected: map[string]interface{}{"a": 1, "b": map[string]interface{}{"c": 2}},
		},
		{
			name:     "nested key path",
			value:    map[string]interface{}{"a": 1},
			keyPath:  []string{"b", "c"},
			metadata: map[string]interface{}{"d": 2},
			expected: map[string]interface{}{
				"a": 1,
				"b": map[string]interface{}{"c": map[string]interface{}{"d": 2}},
			},
		},
		{
			name:        "error: metadata is not a map",
			value:       map[string]interface{}{"a": 1},
			keyPath:     []string{},
			metadata:    "not a map",
			expected:    map[string]interface{}{"a": 1},
			expectedErr: "metadata must be of type map[string]interface{}",
		},
		{
			name:     "overwrite non-map key value",
			value:    map[string]interface{}{"a": 1},
			keyPath:  []string{"a"},
			metadata: map[string]interface{}{"b": 2},
			expected: map[string]interface{}{"a": map[string]interface{}{"b": 2}},
		},
		{
			name:     "overwrite map value with another map value",
			value:    map[string]interface{}{"a": map[string]interface{}{"b": 1}},
			keyPath:  []string{"a"},
			metadata: map[string]interface{}{"b": 2},
			expected: map[string]interface{}{"a": map[string]interface{}{"b": 2}},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			value := make(map[string]interface{})
			for k, v := range tt.value {
				value[k] = v
			}
			err := storeMetadataByPath(value, tt.keyPath, tt.metadata)
			if err == nil && tt.expectedErr != "" || err != nil && err.Error() != tt.expectedErr {
				t.Errorf("Expected error: %v, got: %v", tt.expectedErr, err)
			}
			if !reflect.DeepEqual(tt.expected, value) {
				t.Errorf("Expected value: %v, got: %v", tt.expected, value)
			}
		})
	}
}
