package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"text/template"
)

func parseErrorResponse(err error) string {
	return fmt.Sprintf(`{"error":"%v"}`, err)
}

func respondJSON(w http.ResponseWriter, obj interface{}, statusCode int) {
	jsonStr, err := json.Marshal(obj)
	if err != nil {
		http.Error(w, parseErrorResponse(err), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	_, err = w.Write(jsonStr)
	if err != nil {
		log.Default()
	}
}

func parsePrompt(promptTemplate string, data any) (string, error) {
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

func jsonErrorHandler(err error, _ *http.Request) *http.Response {
	body, _ := json.Marshal(map[string]string{
		"error": err.Error(),
	})
	return &http.Response{
		StatusCode: http.StatusBadRequest,
		Body:       io.NopCloser(bytes.NewReader(body)),
	}
}
