package web

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestTemplateFuncs(t *testing.T) {
	funcs := templateFuncs()

	// Test ToLower
	assert.Equal(
		t,
		"test",
		funcs["ToLower"].(func(string) string)("TEST"),
		"ToLower function failed",
	)

	// Test Add
	assert.Equal(
		t,
		int64(15),
		funcs["Add"].(func(int64, int64) int64)(10, 5),
		"Add function failed",
	)

	// Test Sub
	assert.Equal(t, int64(5), funcs["Sub"].(func(int64, int64) int64)(10, 5), "Sub function failed")
}
