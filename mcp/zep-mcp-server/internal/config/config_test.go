package config

import (
	"os"
	"testing"
)

func TestLoadConfig_MissingAPIKey(t *testing.T) {
	// Save original env
	originalKey := os.Getenv("ZEP_API_KEY")
	defer func() {
		if originalKey != "" {
			_ = os.Setenv("ZEP_API_KEY", originalKey)
		} else {
			_ = os.Unsetenv("ZEP_API_KEY")
		}
	}()

	// Test with missing API key
	_ = os.Unsetenv("ZEP_API_KEY")
	_, err := LoadConfig()
	if err == nil {
		t.Error("LoadConfig() should error when ZEP_API_KEY is missing")
	}
}

func TestLoadConfig_WithAPIKey(t *testing.T) {
	// Save original env
	originalKey := os.Getenv("ZEP_API_KEY")
	defer func() {
		if originalKey != "" {
			_ = os.Setenv("ZEP_API_KEY", originalKey)
		} else {
			_ = os.Unsetenv("ZEP_API_KEY")
		}
	}()

	// Test with API key
	_ = os.Setenv("ZEP_API_KEY", "test-key-123")
	cfg, err := LoadConfig()
	if err != nil {
		t.Errorf("LoadConfig() unexpected error: %v", err)
	}

	if cfg.ZepAPIKey != "test-key-123" {
		t.Errorf("Expected ZepAPIKey='test-key-123', got '%s'", cfg.ZepAPIKey)
	}

	if cfg.ServerName != "zep-mcp-server" {
		t.Errorf("Expected ServerName='zep-mcp-server', got '%s'", cfg.ServerName)
	}

	if cfg.ServerVersion != "0.1.0" {
		t.Errorf("Expected ServerVersion='0.1.0', got '%s'", cfg.ServerVersion)
	}

	if cfg.LogLevel != "info" {
		t.Errorf("Expected LogLevel='info', got '%s'", cfg.LogLevel)
	}
}

func TestLoadConfig_CustomLogLevel(t *testing.T) {
	// Save original env
	originalKey := os.Getenv("ZEP_API_KEY")
	originalLogLevel := os.Getenv("LOG_LEVEL")
	defer func() {
		if originalKey != "" {
			_ = os.Setenv("ZEP_API_KEY", originalKey)
		} else {
			_ = os.Unsetenv("ZEP_API_KEY")
		}
		if originalLogLevel != "" {
			_ = os.Setenv("LOG_LEVEL", originalLogLevel)
		} else {
			_ = os.Unsetenv("LOG_LEVEL")
		}
	}()

	// Test with custom log level
	_ = os.Setenv("ZEP_API_KEY", "test-key-123")
	_ = os.Setenv("LOG_LEVEL", "debug")

	cfg, err := LoadConfig()
	if err != nil {
		t.Errorf("LoadConfig() unexpected error: %v", err)
	}

	if cfg.LogLevel != "debug" {
		t.Errorf("Expected LogLevel='debug', got '%s'", cfg.LogLevel)
	}
}
