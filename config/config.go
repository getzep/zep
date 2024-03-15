package config

import (
	"errors"
	"strings"

	"github.com/getzep/zep/internal"

	"github.com/joho/godotenv"
	"github.com/sirupsen/logrus"
	"github.com/spf13/viper"
)

// We're bootstrapping so avoid any imports from other packages
var log = logrus.New()

// EnvVars is a set of secrets that should be stored in the environment, not config file
var EnvVars = map[string]string{
	"llm.anthropic_api_key": "ZEP_ANTHROPIC_API_KEY",
	"llm.openai_api_key":    "ZEP_OPENAI_API_KEY",
	"auth.secret":           "ZEP_AUTH_SECRET",
	"development":           "ZEP_DEVELOPMENT",
}

// LoadConfig loads the config file and ENV variables into a Config struct
func LoadConfig(configFile string) (*Config, error) {
	if configFile != "" {
		viper.SetConfigFile(configFile)
	} else {
		viper.AddConfigPath(".")
		viper.SetConfigType("yaml")
		viper.SetConfigName("config")
	}

	viper.SetConfigType("yaml")

	viper.SetEnvPrefix("ZEP")
	viper.SetEnvKeyReplacer(strings.NewReplacer(".", "_"))
	viper.AutomaticEnv()

	if err := viper.ReadInConfig(); err != nil {
		// Ignore error if config file not found
		if errors.Is(err, viper.ConfigFileNotFoundError{}) {
			return nil, err
		}
	}

	// Environment variables take precedence over config file
	loadDotEnv()

	// Bind environment variables to config keys
	for key, envVar := range EnvVars {
		bindEnv(key, envVar)
	}

	var cfg Config
	if err := viper.Unmarshal(&cfg); err != nil {
		return nil, err
	}

	return &cfg, nil
}

// loadDotEnv loads environment variables from .env file
func loadDotEnv() {
	err := godotenv.Load()
	if err != nil {
		log.Warn(
			".env file not found or unable to load. This warning can be ignored if Zep is run" +
				" using docker compose with env_file defined or you are passing ENV variables.",
		)
	}
}

// bindEnv binds an environment variable to a config key
func bindEnv(key string, envVar string) {
	err := viper.BindEnv(key, envVar)
	if err != nil {
		log.Fatalf("Error binding environment variable: %s", err)
	}
}

// SetLogLevel sets the log level based on the config file. Defaults to INFO if not set or invalid
func SetLogLevel(cfg *Config) {
	if cfg.Development {
		internal.SetLogLevel(logrus.DebugLevel)
		log.Info("Development mode. Setting log level to: ", logrus.DebugLevel)
		return
	}
	level, err := logrus.ParseLevel(cfg.Log.Level)
	if err != nil {
		level = logrus.InfoLevel
	}
	internal.SetLogLevel(level)
	log.Info("Log level set to: ", level)
}
