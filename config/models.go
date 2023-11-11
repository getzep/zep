package config

// Config holds the configuration of the application
// Use cmd.NewConfig to create a new instance
type Config struct {
	LLM           LLM                 `mapstructure:"llm"`
	NLP           NLP                 `mapstructure:"nlp"`
	Memory        MemoryConfig        `mapstructure:"memory"`
	Extractors    ExtractorsConfig    `mapstructure:"extractors"`
	Store         StoreConfig         `mapstructure:"store"`
	Server        ServerConfig        `mapstructure:"server"`
	Log           LogConfig           `mapstructure:"log"`
	Auth          AuthConfig          `mapstructure:"auth"`
	OpenTelemetry OpenTelemetryConfig `mapstructure:"opentelemetry"`
	DataConfig    DataConfig          `mapstructure:"data"`
	Development   bool                `mapstructure:"development"`
	CustomPrompts CustomPromptsConfig `mapstructure:"custom_prompts"`
}

type StoreConfig struct {
	Type     string         `mapstructure:"type"`
	Postgres PostgresConfig `mapstructure:"postgres"`
}

type LLM struct {
	Service             string            `mapstructure:"service"`
	Model               string            `mapstructure:"model"`
	AnthropicAPIKey     string            `mapstructure:"anthropic_api_key"`
	OpenAIAPIKey        string            `mapstructure:"openai_api_key"`
	AzureOpenAIEndpoint string            `mapstructure:"azure_openai_endpoint"`
	AzureOpenAIModel    AzureOpenAIConfig `mapstructure:"azure_openai"`
	OpenAIEndpoint      string            `mapstructure:"openai_endpoint"`
	OpenAIOrgID         string            `mapstructure:"openai_org_id"`
}

type AzureOpenAIConfig struct {
	LLMDeployment       string `mapstructure:"llm_deployment"`
	EmbeddingDeployment string `mapstructure:"embedding_deployment"`
}

type NLP struct {
	ServerURL string `mapstructure:"server_url"`
}

type MemoryConfig struct {
	MessageWindow int `mapstructure:"message_window"`
}

type PostgresConfig struct {
	DSN              string           `mapstructure:"dsn"`
	AvailableIndexes AvailableIndexes `mapstructure:"available_indexes"`
}

type AvailableIndexes struct {
	IVFFLAT bool `mapstructure:"ivfflat"`
	HSNW    bool `mapstructure:"hsnw"`
}

type ServerConfig struct {
	Host           string `mapstructure:"host"`
	Port           int    `mapstructure:"port"`
	WebEnabled     bool   `mapstructure:"web_enabled"`
	MaxRequestSize int64  `mapstructure:"max_request_size"`
}

type LogConfig struct {
	Level string `mapstructure:"level"`
}

type OpenTelemetryConfig struct {
	Enabled bool `mapstructure:"enabled"`
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
	Enabled    bool                  `mapstructure:"enabled"`
	Embeddings EmbeddingsConfig      `mapstructure:"embeddings"`
	Entities   EntityExtractorConfig `mapstructure:"entities"`
}

type CustomPromptsConfig struct {
	SummarizerPrompts ExtractorPromptsConfig `mapstructure:"summarizer_prompts"`
}

type ExtractorPromptsConfig struct {
	OpenAI    string `mapstructure:"openai"`
	Anthropic string `mapstructure:"anthropic"`
}

type EmbeddingsConfig struct {
	Enabled    bool   `mapstructure:"enabled"`
	Dimensions int    `mapstructure:"dimensions"`
	Service    string `mapstructure:"service"`
	// ChunkSize is the number of documents to embed in a single task.
	ChunkSize int `mapstructure:"chunk_size"`
}

type EntityExtractorConfig struct {
	Enabled bool `mapstructure:"enabled"`
}

type IntentExtractorConfig struct {
	Enabled bool `mapstructure:"enabled"`
}
