package transform

import "fmt"

// ValidateRequired checks if a required parameter exists and has the correct type
func ValidateRequired(params map[string]interface{}, key string) (string, error) {
	value, ok := params[key]
	if !ok {
		return "", fmt.Errorf("%s is required", key)
	}

	strValue, ok := value.(string)
	if !ok {
		return "", fmt.Errorf("%s must be a string", key)
	}

	if strValue == "" {
		return "", fmt.Errorf("%s cannot be empty", key)
	}

	return strValue, nil
}

// GetOptionalString retrieves an optional string parameter with a default value
func GetOptionalString(params map[string]interface{}, key, defaultValue string) string {
	if value, ok := params[key]; ok {
		if strValue, ok := value.(string); ok && strValue != "" {
			return strValue
		}
	}
	return defaultValue
}

// GetOptionalInt retrieves an optional integer parameter with a default value
func GetOptionalInt(params map[string]interface{}, key string, defaultValue int) int {
	if value, ok := params[key]; ok {
		// Handle both float64 (JSON numbers) and int
		switch v := value.(type) {
		case float64:
			return int(v)
		case int:
			return v
		}
	}
	return defaultValue
}

// GetOptionalFloat retrieves an optional float parameter with a default value
func GetOptionalFloat(params map[string]interface{}, key string, defaultValue float64) float64 {
	if value, ok := params[key]; ok {
		// Handle both float64 and int
		switch v := value.(type) {
		case float64:
			return v
		case int:
			return float64(v)
		}
	}
	return defaultValue
}

// GetOptionalStringSlice retrieves an optional string slice parameter
func GetOptionalStringSlice(params map[string]interface{}, key string) []string {
	if value, ok := params[key]; ok {
		if slice, ok := value.([]interface{}); ok {
			result := make([]string, 0, len(slice))
			for _, item := range slice {
				if str, ok := item.(string); ok {
					result = append(result, str)
				}
			}
			return result
		}
	}
	return nil
}
