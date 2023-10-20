package config

import (
	"testing"

	"github.com/invopop/jsonschema"
	"github.com/stretchr/testify/assert"
)

func TestJSONSchema(t *testing.T) {
	schemaJSON, err := JSONSchema()
	assert.NoError(t, err)
	assert.NotNil(t, schemaJSON)

	unmarshalledSchema := &jsonschema.Schema{}
	err = unmarshalledSchema.UnmarshalJSON(schemaJSON)
	assert.NoError(t, err)
}
