//go:build testutils

package testutils

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"github.com/joho/godotenv"
	"github.com/oiime/logrusbun"
	"github.com/sirupsen/logrus"
	"github.com/uptrace/bun"

	"github.com/getzep/zep/config"
)

// testConfigDefaults returns a config.Config with default values for testing.
// It also loads secrets from .env file or environment variables.
func testConfigDefaults() (*config.Config, error) {
	testConfig := &config.Config{
		LLM: config.LLM{
			Service: "openai",
			Model:   "gpt-3.5-turbo-1106",
		},
		NLP: config.NLP{
			ServerURL: "http://localhost:5557",
		},
		Memory: config.MemoryConfig{
			MessageWindow: 12,
		},
		Extractors: config.ExtractorsConfig{
			Documents: config.DocumentExtractorsConfig{
				Embeddings: config.EmbeddingsConfig{
					Enabled:    true,
					Dimensions: 1536,
					Service:    "openai",
				},
			},
			Messages: config.MessageExtractorsConfig{
				Summarizer: config.SummarizerConfig{
					Enabled: true,
					Embeddings: config.EmbeddingsConfig{
						Enabled:    true,
						Dimensions: 1536,
						Service:    "openai",
					},
				},
				Embeddings: config.EmbeddingsConfig{
					Enabled:    true,
					Dimensions: 1536,
					Service:    "openai",
				},
			},
		},
		Store: config.StoreConfig{
			Type: "postgres",
			Postgres: config.PostgresConfig{
				DSN: "postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable",
			},
		},
		Server: config.ServerConfig{
			Host:           "0.0.0.0",
			Port:           8000,
			WebEnabled:     true,
			MaxRequestSize: 1 << 20, // 10MB
		},
		Auth: config.AuthConfig{
			Secret:   "do-not-use-this-secret-in-production",
			Required: false,
		},
		DataConfig: config.DataConfig{
			PurgeEvery: 60,
		},
		Log: config.LogConfig{
			Level: "info",
		},
	}

	projectRoot, err := FindProjectRoot()
	if err != nil {
		return nil, fmt.Errorf("failed to find project root: %v", err)
	}

	// load env vars from .env
	err = godotenv.Load(filepath.Join(projectRoot, ".env"))
	if err != nil {
		fmt.Println(".env file not found or unable to load")
	}

	// Load secrets from environment variables
	for key, envVar := range config.EnvVars {
		switch key {
		case "llm.anthropic_api_key":
			testConfig.LLM.AnthropicAPIKey = os.Getenv(envVar)
		case "llm.openai_api_key":
			testConfig.LLM.OpenAIAPIKey = os.Getenv(envVar)
		case "auth.secret":
			testConfig.Auth.Secret = os.Getenv(envVar)
		case "development":
			testConfig.Development = os.Getenv(envVar) == "true"
		}
	}

	// load postgres config from env
	p := os.Getenv("ZEP_STORE_POSTGRES_DSN")
	if p != "" {
		testConfig.Store.Postgres.DSN = p
	}

	// load nlp server config from env
	n := os.Getenv("ZEP_NLP_SERVER_URL")
	if n != "" {
		testConfig.NLP.ServerURL = n
	}

	return testConfig, nil
}

func NewTestConfig() *config.Config {
	c, err := testConfigDefaults()
	if err != nil {
		panic(err)
	}
	return c
}

func GenerateRandomSessionID(length int) (string, error) {
	bytes := make([]byte, (length+1)/2)
	_, err := rand.Read(bytes)
	if err != nil {
		return "", fmt.Errorf("failed to generate random session ID: %w", err)
	}
	return hex.EncodeToString(bytes)[:length], nil
}

// FindProjectRoot returns the absolute path to the project root directory.
func FindProjectRoot() (string, error) {
	_, currentFilePath, _, ok := runtime.Caller(0)
	if !ok {
		return "", fmt.Errorf("could not get current file path")
	}

	dir := filepath.Dir(currentFilePath)

	for {
		// Check if the current directory contains a marker file or directory that indicates the project root.
		// In this case, we use "go.mod" as an example, but you can use any other marker.
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir, nil
		}

		// If we've reached the top-level directory, the project root is not found.
		if dir == filepath.Dir(dir) {
			return "", fmt.Errorf("project root not found")
		}

		// Move up one directory level.
		dir = filepath.Dir(dir)
	}
}

func SetUpDBLogging(db *bun.DB, log logrus.FieldLogger) {
	db.AddQueryHook(logrusbun.NewQueryHook(logrusbun.QueryHookOptions{
		LogSlow:         time.Second,
		Logger:          log,
		QueryLevel:      logrus.InfoLevel,
		ErrorLevel:      logrus.ErrorLevel,
		SlowLevel:       logrus.WarnLevel,
		MessageTemplate: "{{.Operation}}[{{.Duration}}]: {{.Query}}",
		ErrorTemplate:   "{{.Operation}}[{{.Duration}}]: {{.Query}}: {{.Error}}",
	}))
}

const charset = "abcdefghijklmnopqrstuvwxyz" +
	"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

func GenerateRandomString(length int) string {
	b := make([]byte, length)
	for i := range b {
		bigInt, _ := rand.Int(rand.Reader, big.NewInt(int64(len(charset))))
		b[i] = charset[bigInt.Int64()]
	}
	return string(b)
}
