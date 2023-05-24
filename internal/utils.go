package internal

import (
	"bytes"
	"reflect"
	"text/template"
)

func ParsePrompt(promptTemplate string, data any) (string, error) {
	tmpl, err := template.New("prompt").Parse(promptTemplate)
	if err != nil {
		return "", err
	}

	var buf bytes.Buffer
	err = tmpl.Execute(&buf, data)
	if err != nil {
		return "", err
	}

	return buf.String(), nil
}

func ReverseSlice[T any](slice []T) {
	for i, x := range slice[:len(slice)/2] {
		opp := len(slice) - 1 - i
		slice[i], slice[opp] = slice[opp], x
	}
}

// StructToMap converts a struct to a map, recursively handling nested structs
func StructToMap(item interface{}) map[string]interface{} {
	val := reflect.ValueOf(item)

	// Check if this is a slice of structs
	if val.Kind() == reflect.Slice {
		sliceOut := make([]interface{}, val.Len())
		for i := 0; i < val.Len(); i++ {
			sliceVal := val.Index(i)
			if sliceVal.Kind() == reflect.Struct {
				sliceOut[i] = StructToMap(sliceVal.Interface())
			} else {
				sliceOut[i] = sliceVal.Interface()
			}
		}
		return map[string]interface{}{"data": sliceOut}
	}

	// Dereference pointer to struct
	if val.Kind() == reflect.Ptr {
		val = val.Elem()
	}

	if val.Kind() != reflect.Struct {
		return map[string]interface{}{}
	}

	out := make(map[string]interface{})
	typeOfT := val.Type()

	for i := 0; i < val.NumField(); i++ {
		field := typeOfT.Field(i)
		value := val.Field(i)

		// Recursively handle nested struct
		if value.Kind() == reflect.Struct {
			out[field.Name] = StructToMap(value.Interface())
		} else if value.Kind() == reflect.Slice {
			sliceOut := make([]interface{}, value.Len())
			for i := 0; i < value.Len(); i++ {
				sliceVal := value.Index(i)
				if sliceVal.Kind() == reflect.Struct {
					sliceOut[i] = StructToMap(sliceVal.Interface())
				} else {
					sliceOut[i] = sliceVal.Interface()
				}
			}
			out[field.Name] = sliceOut
		} else {
			out[field.Name] = value.Interface()
		}
	}

	return out
}
