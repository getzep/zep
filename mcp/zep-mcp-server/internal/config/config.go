package config

import (
	"fmt"
	"os"

	"github.com/joho/godotenv"
)

// Config holds the server configuration
type Config struct {
	ZepAPIKey     string // Zep Cloud API key (required)
	LogLevel      string
	ServerName    string
	ServerVersion string
	TransportMode string // "http" or "stdio"
	HTTPPort      int    // HTTP port (when using HTTP transport)
}

// LoadConfig loads configuration from environment variables and .env files
// Environment variables take precedence over .env file values
func LoadConfig() (*Config, error) {
	// Load .env file if present (silently ignore if not found)
	// Looks for .env in current directory
	_ = godotenv.Load()

	apiKey := os.Getenv("ZEP_API_KEY")
	if apiKey == "" {
		return nil, fmt.Errorf("ZEP_API_KEY environment variable is required (set in environment or .env file)")
	}

	return &Config{
		ZepAPIKey:     apiKey,
		LogLevel:      getEnvOrDefault("LOG_LEVEL", "info"),
		ServerName:    "zep-mcp-server",
		ServerVersion: "0.1.0",
	}, nil
}

func getEnvOrDefault(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}
