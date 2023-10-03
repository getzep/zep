package web

import (
	"encoding/json"
	"html/template"
	"reflect"

	"github.com/dustin/go-humanize"
	"github.com/getzep/sprig/v3"
)

func percent(a, b int) int {
	if b == 0 {
		return 0
	}
	return int(float32(a) / float32(b) * 100)
}

// JSONSerializeHTML serializes a map to a JSON string and outputs as HTML
func JSONSerializeHTML(data map[string]interface{}) (template.HTML, error) {
	// make the data safe for HTML
	data = HTMLEscapeMap(data)

	jsonBytes, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return "", err
	}

	highlightedJSON, err := CodeHighlight(string(jsonBytes), "json")
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

// HTMLEscapeStruct recursively walks a struct and any child structs and HTML escapes any string fields
func HTMLEscapeStruct(data interface{}) interface{} {
	v := reflect.ValueOf(data)

	switch v.Kind() {
	case reflect.String:
		return template.HTMLEscapeString(v.String())
	case reflect.Struct:
		for i := 0; i < v.NumField(); i++ {
			field := v.Field(i)
			if field.CanSet() {
				switch field.Kind() {
				case reflect.String:
					field.SetString(template.HTMLEscapeString(field.String()))
				case reflect.Struct:
					HTMLEscapeStruct(field.Interface())
				}
			}
		}
	}
	return data
}

func TemplateFuncs() template.FuncMap {
	funcMap := template.FuncMap{
		"Percent":      percent,
		"ToJSON":       JSONSerializeHTML,
		"CommaInt":     humanize.Comma,
		"RelativeTime": humanize.Time,
	}

	for k, v := range sprig.FuncMap() {
		funcMap[k] = v
	}
	return funcMap
}
