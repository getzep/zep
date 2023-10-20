package config

import (
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/invopop/jsonschema"
)

func TestJsonSchema(t *testing.T) {
	schemaJson, err := JsonSchema()

	assert.NoError(t, err)
	assert.NotNil(t, schemaJson)
	unmarshalledSchema := &jsonschema.Schema{}
	err = unmarshalledSchema.UnmarshalJSON(schemaJson)
	assert.NoError(t, err)
}
