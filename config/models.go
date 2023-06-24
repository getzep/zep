package config

// Config holds the configuration of the application
// Use cmd.NewConfig to create a new instance
type Config struct {
	LLM         LLM               `mapstructure:"llm"`
	NLP         NLP               `mapstructure:"nlp"`
	Memory      MemoryConfig      `mapstructure:"memory"`
	Extractors  ExtractorsConfig  `mapstructure:"extractors"`
	MemoryStore MemoryStoreConfig `mapstructure:"memory_store"`
	Server      ServerConfig      `mapstructure:"server"`
	Log         LogConfig         `mapstructure:"log"`
	Auth        AuthConfig        `mapstructure:"auth"`
}

type MemoryStoreConfig struct {
	Type     string         `mapstructure:"type"`
	Postgres PostgresConfig `mapstructure:"postgres"`
}

type LLM struct {
	Model string `mapstructure:"model"`
	// OpenAIAPIKey is loaded from ENV not config file.
	OpenAIAPIKey string `mapstructure:"openai_api_key"`
}

type NLP struct {
	ServerURL string `mapstructure:"server_url"`
}

type MemoryConfig struct {
	MessageWindow int `mapstructure:"message_window"`
}

type PostgresConfig struct {
	DSN string `mapstructure:"dsn"`
}

type ServerConfig struct {
	Port int `mapstructure:"port"`
}

type LogConfig struct {
	Level string `mapstructure:"level"`
}

type AuthConfig struct {
	Secret   string `mapstructure:"secret"`
	Required bool   `mapstructure:"required"`
}

// ExtractorsConfig holds the configuration for all extractors
type ExtractorsConfig struct {
	Summarizer SummarizerConfig      `mapstructure:"summarizer"`
	Embeddings EmbeddingsConfig      `mapstructure:"embeddings"`
	Entities   EntityExtractorConfig `mapstructure:"entities"`
	Intent     IntentExtractorConfig `mapstructure:"intent"`
}

type SummarizerConfig struct {
	Enabled bool `mapstructure:"enabled"`
}

type EmbeddingsConfig struct {
	Enabled    bool   `mapstructure:"enabled"`
	Dimensions int    `mapstructure:"dimensions"`
	Model      string `mapstructure:"model"`
}

type EntityExtractorConfig struct {
	Enabled bool `mapstructure:"enabled"`
}

type IntentExtractorConfig struct {
	Enabled bool `mapstructure:"enabled"`
}
