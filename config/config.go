package config

import (
	"strings"

	"github.com/getzep/zep/internal"

	"github.com/joho/godotenv"
	"github.com/sirupsen/logrus"
	"github.com/spf13/viper"
)

// We're bootstrapping so avoid any imports from other packages
var log = logrus.New()

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
		return nil, err
	}

	// Environment variables take precedence over config file
	loadDotEnv()

	err := viper.BindEnv("llm.openai_api_key", "ZEP_OPENAI_API_KEY")
	if err != nil {
		log.Fatalf("Error binding environment variable: %s", err)
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
		log.Warn(".env file not found or unable to load")
	}
}

// SetLogLevel sets the log level based on the config file. Defaults to INFO if not set or invalid
func SetLogLevel(cfg *Config) {
	level, err := logrus.ParseLevel(cfg.Log.Level)
	if err != nil {
		level = logrus.InfoLevel
	}
	internal.SetLogLevel(level)
	log.Info("Log level set to: ", level)
}
