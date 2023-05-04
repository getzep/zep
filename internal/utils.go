package internal

import (
	"bytes"
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

func ReverseSlice[T comparable](slice []T) {
	for i, x := range slice[:len(slice)/2] {
		opp := len(slice) - 1 - i
		slice[i], slice[opp] = slice[opp], x
	}
}
