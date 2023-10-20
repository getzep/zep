package config

import (
	"errors"

	"github.com/invopop/jsonschema"
)

var (
	ErrGeneratedSchemaIsNil = errors.New("generated JSON Schema is nil")
)

func JSONSchema() ([]byte, error) {
	schema := jsonschema.Reflect(&Config{})

	if schema == nil {
		return nil, ErrGeneratedSchemaIsNil
	}

	return schema.MarshalJSON()
}
