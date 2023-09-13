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

func templateFuncs() template.FuncMap {
	return template.FuncMap{
		"ToLower": strings.ToLower,
		"Add":     add,
		"Sub":     sub,
		"Div":     div,
		"Product": product,
		"Percent": percent,
		"Mod":     mod,
	}
}
