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

type ExtractorsConfig struct {
	Summarizer SummarizerConfig      `mapstructure:"summarizer"`
	Embeddings EmbeddingsConfig      `mapstructure:"embeddings"`
	Entities   EntityExtractorConfig `mapstructure:"entities"`
}

type SummarizerConfig struct {
	Enabled bool `mapstructure:"enabled"`
}

type EmbeddingsConfig struct {
	Messages  TextEmbeddingsConfig `mapstructure:"messages"`
	Documents TextEmbeddingsConfig `mapstructure:"documents"`
}

type TextEmbeddingsConfig struct {
	Enabled  bool   `mapstructure:"enabled"`
	Provider string `mapstructure:"provider"`
}

type EntityExtractorConfig struct {
	Enabled bool `mapstructure:"enabled"`
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
