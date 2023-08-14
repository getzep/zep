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

// StructToMap converts a struct to a map, recursively handling nested structs or lists of structs.
func StructToMap(item interface{}) map[string]interface{} {
	val := reflect.ValueOf(item)

	processSlice := func(val reflect.Value) []interface{} {
		sliceOut := make([]interface{}, val.Len())
		for i := 0; i < val.Len(); i++ {
			sliceVal := val.Index(i)
			if sliceVal.Kind() == reflect.Struct {
				sliceOut[i] = StructToMap(sliceVal.Interface())
			} else {
				sliceOut[i] = sliceVal.Interface()
			}
		}
		return sliceOut
	}

	switch val.Kind() {
	case reflect.Slice:
		return map[string]interface{}{"data": processSlice(val)}
	case reflect.Ptr:
		val = val.Elem()
		if val.Kind() != reflect.Struct {
			return map[string]interface{}{}
		}
	default:
		if val.Kind() != reflect.Struct {
			return map[string]interface{}{}
		}
	}

	out := make(map[string]interface{})
	typeOfT := val.Type()

	for i := 0; i < val.NumField(); i++ {
		field := typeOfT.Field(i)
		value := val.Field(i)

		switch value.Kind() {
		case reflect.Struct:
			out[field.Name] = StructToMap(value.Interface())
		case reflect.Slice:
			out[field.Name] = processSlice(value)
		default:
			out[field.Name] = value.Interface()
		}
	}

	return out
}

func MergeMaps[T any](maps ...map[string]T) map[string]T {
	result := make(map[string]T)
	for _, m := range maps {
		for k, v := range m {
			result[k] = v
		}
	}
	return result
}
