package webhandlers

import (
	"encoding/json"
	"errors"
	"html/template"
	"net/http"
	"regexp"

	"github.com/getzep/zep/pkg/web"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/pkg/models"
)

type ConfigData struct {
	ConfigHTML   template.HTML
	ConfigString string
}

// redactHTMLEncodeConfig redacts sensitive config values and HTML escapes the config
func redactHTMLEncodeConfig(cfg *config.Config) (*config.Config, error) {
	redactedConfig := *cfg
	redactedConfig.LLM.AnthropicAPIKey = "**redacted**"
	redactedConfig.LLM.OpenAIAPIKey = "**redacted**"
	redactedConfig.Auth.Secret = "**redacted**"

	re := regexp.MustCompile(`(?i)(postgres://[^:]+:)([^@]+)`)
	redactedConfig.Store.Postgres.DSN = re.ReplaceAllString(
		redactedConfig.Store.Postgres.DSN,
		"$1**redacted**",
	)

	escapedConfig := web.HTMLEscapeStruct(redactedConfig)

	if redactedConfig, ok := escapedConfig.(config.Config); ok {
		return &redactedConfig, nil
	}

	return nil, errors.New("failed to redact config")
}

// getConfigJSONAndHTML returns the config as a JSON string and HTML escaped string
func getConfigJSONAndHTML(cfg *config.Config) (string, string, error) {
	cfgBytes, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return "", "", err
	}

	configHTML, err := web.CodeHighlight(string(cfgBytes), "json")
	if err != nil {
		return "", "", err
	}

	return configHTML, string(cfgBytes), nil
}

func GetSettingsHandler(appState *models.AppState) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		path := "/admin/settings"

		redactedConfig, err := redactHTMLEncodeConfig(appState.Config)
		if err != nil {
			handleError(w, err, "failed to redact config")
			return
		}

		configHTML, configJSON, err := getConfigJSONAndHTML(redactedConfig)
		if err != nil {
			handleError(w, err, "failed to get config HTML")
			return
		}

		configData := ConfigData{
			ConfigHTML:   template.HTML(configHTML), //nolint: gosec
			ConfigString: configJSON,
		}

		renderSettingsPage(w, r, path, configData)
	}
}

func renderSettingsPage(
	w http.ResponseWriter,
	r *http.Request,
	path string,
	configData ConfigData,
) {
	page := web.NewPage(
		"Settings",
		"How Zep is currently configured",
		path,
		[]string{
			"templates/pages/settings.html",
			"templates/components/content/*.html",
		},
		[]web.BreadCrumb{
			{
				Title: "Settings",
				Path:  path,
			},
		},
		configData,
	)

	page.Render(w, r)
}