package web

import (
	"encoding/json"
	"errors"
	"html/template"
	"strings"
)

func add(a, b int64) int64 {
	return a + b
}

func sub(a, b int64) int64 {
	return a - b
}

// returns 0 on a divide by 0
func div(a, b int) float32 {
	if b == 0 {
		return 0
	}
	return float32(a) / float32(b)
}

func product(a, b float32) float32 {
	return a * b
}

func percent(a, b int) int {
	if b == 0 {
		return 0
	}
	return int(float32(a) / float32(b) * 100)
}

func mod(a, b int) int {
	if b == 0 {
		return 0
	}
	return a % b
}

// dict is a helper function to create a map[string]interface{} in a template
func dict(values ...interface{}) (map[string]interface{}, error) {
	if len(values)%2 != 0 {
		return nil, errors.New("invalid dict call")
	}
	dict := make(map[string]interface{}, len(values)/2)
	for i := 0; i < len(values); i += 2 {
		key, ok := values[i].(string)
		if !ok {
			return nil, errors.New("dict keys must be strings")
		}
		dict[key] = values[i+1]
	}
	return dict, nil
}

// JSONSerializeHTML serializes a map to a JSON string and outputs as HTML
func JSONSerializeHTML(data map[string]interface{}) (template.HTML, error) {
	// make the data safe for HTML
	data = HTMLEscapeMap(data)

	jsonBytes, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return "", err
	}

	highlightedJSON, err := codeHighlight(string(jsonBytes), "json")
	if err != nil {
		return "", err
	}

	// Convert buffer to string
	return template.HTML(highlightedJSON), err //nolint: gosec
}

// HTMLEscapeMap recursively walks a map and HTML escapes any string fields
func HTMLEscapeMap(data map[string]interface{}) map[string]interface{} {
	for key, value := range data {
		switch v := value.(type) {
		case string:
			data[key] = template.HTMLEscapeString(v)
		case map[string]interface{}:
			data[key] = HTMLEscapeMap(v)
		default:
			// do nothing for other types
		}
	}
	return data
}

func templateFuncs() template.FuncMap {
	return template.FuncMap{
		"ToLower": strings.ToLower,
		"Add":     add,
		"Sub":     sub,
		"Div":     div,
		"Product": product,
		"Percent": percent,
		"Mod":     mod,
		"Dict":    dict,
		"ToJSON":  JSONSerializeHTML,
	}
}
