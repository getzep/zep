package web

import (
	"html/template"
	"strings"
)

func add(a, b int64) int64 {
	return a + b
}

func sub(a, b int64) int64 {
	return a - b
}

func templateFuncs() template.FuncMap {
	return template.FuncMap{
		"ToLower": strings.ToLower,
		"Add":     add,
		"Sub":     sub,
	}
}
