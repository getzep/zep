package config

import (
	"fmt"
	"os"
	"strings"
	"text/template"
)

func parseConfigTemplate(data []byte) ([]byte, error) { //nolint:unused // this is only called in CE
	var missingVars []string

	tmpl, err := template.New("config").Funcs(template.FuncMap{
		"Env": func(key string) string {
			val := os.Getenv(key)
			if val == "" {
				missingVars = append(missingVars, key)
			}

			return val
		},
	}).Parse(string(data))
	if err != nil {
		return nil, err
	}

	var result strings.Builder

	err = tmpl.Execute(&result, nil)
	if err != nil {
		return nil, err
	}

	if len(missingVars) > 0 {
		return nil, fmt.Errorf("missing environmentvariables: %s", strings.Join(missingVars, ", "))
	}

	return []byte(result.String()), nil
}
