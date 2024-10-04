
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

// Load loads the config from the given filename.
// This function will panic if the config file cannot be loaded or if the
// config file is not valid.
// Load should be called as early as possible in the application lifecycle and
// before any config options are used.
func Load() {
	location := os.Getenv("ZEP_CONFIG_FILE")
	if location == "" {
		wd, _ := os.Getwd()
		location = filepath.Join(wd, "zep.yaml")
	}

	data, err := os.ReadFile(location)
	if err != nil {
		panic(fmt.Errorf("config file could not be read: %w", err))
	}

	data, err = parseConfigTemplate(data)
	if err != nil {
		panic(fmt.Errorf("error processing config file: %w", err))
	}

	config := defaultConfig
	if err := yaml.Unmarshal(data, &config); err != nil {
		panic(fmt.Errorf("config file contains invalid yaml: %w", err))
	}

	if err := cleanAndValidateConfig(&config); err != nil {
		panic(fmt.Errorf("config file is invalid: %w", err))
	}

	_loaded = &config
}

func cleanAndValidateConfig(config *Config) error {
	secret := strings.TrimSpace(config.ApiSecret)
	if secret == "" {
		return fmt.Errorf("api_secret is not set")
	}

	config.ApiSecret = secret

	return nil
}
