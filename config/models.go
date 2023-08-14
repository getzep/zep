package config

// Config holds the configuration of the application
// Use cmd.NewConfig to create a new instance
type Config struct {
	LLM        LLM              `mapstructure:"llm"`
	NLP        NLP              `mapstructure:"nlp"`
	Memory     MemoryConfig     `mapstructure:"memory"`
	Extractors ExtractorsConfig `mapstructure:"extractors"`
	Store      StoreConfig      `mapstructure:"store"`
	Server     ServerConfig     `mapstructure:"server"`
	Log        LogConfig        `mapstructure:"log"`
	Auth       AuthConfig       `mapstructure:"auth"`
	DataConfig DataConfig       `mapstructure:"data"`
}

type StoreConfig struct {
	Type     string         `mapstructure:"type"`
	Postgres PostgresConfig `mapstructure:"postgres"`
}

type LLM struct {
	Service             string `mapstructure:"service"`
	Model               string `mapstructure:"model"`
	AnthropicAPIKey     string `mapstructure:"anthropic_api_key"`
	OpenAIAPIKey        string `mapstructure:"openai_api_key"`
	AzureOpenAIEndpoint string `mapstructure:"azure_openai_endpoint"`
	OpenAIEndpoint      string `mapstructure:"openai_endpoint"`
	OpenAIOrgID         string `mapstructure:"openai_org_id"`
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

type DataConfig struct {
	// PurgeEvery is the period between hard deletes, in minutes.
	// If set to 0, hard deletes will not be performed.
	PurgeEvery int `mapstructure:"purge_every"`
}

type ExtractorsConfig struct {
	Messages  MessageExtractorsConfig  `mapstructure:"messages"`
	Documents DocumentExtractorsConfig `mapstructure:"documents"`
}

// MessageExtractorsConfig holds the configuration for all extractors
type MessageExtractorsConfig struct {
	Summarizer SummarizerConfig      `mapstructure:"summarizer"`
	Embeddings EmbeddingsConfig      `mapstructure:"embeddings"`
	Entities   EntityExtractorConfig `mapstructure:"entities"`
	Intent     IntentExtractorConfig `mapstructure:"intent"`
}

type DocumentExtractorsConfig struct {
	Embeddings EmbeddingsConfig `mapstructure:"embeddings"`
}

type SummarizerConfig struct {
	Enabled bool `mapstructure:"enabled"`
}

type EmbeddingsConfig struct {
	Enabled    bool   `mapstructure:"enabled"`
	Dimensions int    `mapstructure:"dimensions"`
	Service    string `mapstructure:"service"`
}

type EntityExtractorConfig struct {
	Enabled bool `mapstructure:"enabled"`
}

type IntentExtractorConfig struct {
	Enabled bool `mapstructure:"enabled"`
}
