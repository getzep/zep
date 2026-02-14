package transform

import (
	"encoding/json"
	"fmt"
)

// UnmarshalParams unmarshals JSON raw message into a map of parameters
func UnmarshalParams(raw json.RawMessage) (map[string]interface{}, error) {
	if len(raw) == 0 {
		return make(map[string]interface{}), nil
	}

	var params map[string]interface{}
	if err := json.Unmarshal(raw, &params); err != nil {
		return nil, fmt.Errorf("failed to unmarshal parameters: %w", err)
	}
	return params, nil
}
