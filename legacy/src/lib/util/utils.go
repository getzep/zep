package util

import (
	"bytes"
	"math/rand"
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

// StructToMap converts a struct to a map, recursively handling nested structs or lists of structs.
func StructToMap(item any) map[string]any {
	val := reflect.ValueOf(item)

	processSlice := func(val reflect.Value) []any {
		sliceOut := make([]any, val.Len())
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
		return map[string]any{"data": processSlice(val)}
	case reflect.Ptr:
		val = val.Elem()
		if val.Kind() != reflect.Struct {
			return map[string]any{}
		}
	default:
		if val.Kind() != reflect.Struct {
			return map[string]any{}
		}
	}

	out := make(map[string]any)
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

// ShuffleSlice shuffles a slice in place.
func ShuffleSlice[T any](a []T) {
	rand.Shuffle(len(a), func(i, j int) { a[i], a[j] = a[j], a[i] })
}

func IsInterfaceNilValue(i any) bool {
	return i == nil || reflect.ValueOf(i).IsNil()
}

type ptrTypes interface {
	int | int32 | int64 | float32 | float64 | bool | string
}

func AsPtr[T ptrTypes](value T) *T {
	return &value
}

// SafelyDereference safely dereferences a pointer of any type T.
// It returns the value pointed to if the pointer is not nil, otherwise it returns the zero value of T.
func SafelyDereference[T any](ptr *T) T {
	if ptr != nil {
		return *ptr // Dereference the pointer and return the value
	}
	var zero T  // Initialize a variable with the zero value of type T
	return zero // Return the zero value if ptr is nil
}
