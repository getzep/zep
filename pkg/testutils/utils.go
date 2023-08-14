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

	"github.com/oiime/logrusbun"
	"github.com/sirupsen/logrus"
	"github.com/uptrace/bun"

	"github.com/spf13/viper"

	"github.com/joho/godotenv"

	"github.com/getzep/zep/config"
)

var testConfig *config.Config

func init() {
	var err error
	testConfig, err = initConfig()
	if err != nil {
		panic(err)
	}
}

func GetDSN() string {
	var testDsn = "postgres://postgres:postgres@localhost:5432/postgres?sslmode=disable"
	dsnFromEnv := viper.GetString("store.postgres.dsn")
	if dsnFromEnv != "" {
		return dsnFromEnv
	}
	return testDsn
}

func initConfig() (*config.Config, error) {
	projectRoot, err := FindProjectRoot()
	if err != nil {
		return nil, fmt.Errorf("failed to find project root: %v", err)
	}
	// load env vars from .env
	err = godotenv.Load(filepath.Join(projectRoot, ".env"))
	if err != nil {
		fmt.Println(".env file not found or unable to load")
	}
	configPath := filepath.Join(projectRoot, "config.yaml")
	cfg, err := config.LoadConfig(configPath)
	if err != nil {
		return nil, fmt.Errorf("failed to load config: %v", err)
	}

	cfg.LLM.Service = "openai"
	cfg.LLM.Model = "gpt-3.5-turbo"

	return cfg, nil
}

func NewTestConfig() *config.Config {
	return testConfig
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
