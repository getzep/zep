package cmd

import (
	"github.com/sirupsen/logrus"
	"os"

	"strings"

	"github.com/danielchalef/zep/internal"
	"github.com/joho/godotenv"
	"github.com/spf13/cobra"
	"github.com/spf13/viper"
)

var (
	log = internal.GetLogger()

	cfgFile string
)

var cmd = &cobra.Command{
	Use:   "zep",
	Short: "zep manages memory and retrieval",
	Run:   func(cmd *cobra.Command, args []string) { run() },
}

func init() {
	cobra.OnInitialize(initConfig)

	cmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file (default config.yaml)")
}

// Execute executes the root cobra command.
func Execute() {
	err := cmd.Execute()

	if err != nil {
		os.Exit(1)
	}
}

// initConfig reads in config file and ENV variables if set.
// Prcedence is given to ENV variables, then config file, then defaults.
func initConfig() {
	if cfgFile != "" {
		// Use config file from the flag.
		viper.SetConfigFile(cfgFile)
	} else {
		viper.AddConfigPath(".")
		viper.SetConfigType("yaml")
		viper.SetConfigName("config")
	}

	setDefaults()

	if err := viper.ReadInConfig(); err != nil {
		log.Fatalf("Error reading config file: %s", err)
	}
	// Environment variables take precedence over config file
	// read in .env file if present
	loadEnv()
	viper.SetEnvPrefix(
		"ZEP",
	)
	viper.SetEnvKeyReplacer(strings.NewReplacer(`.`, `_`)) // replaced nested . with _
	viper.AutomaticEnv()
	// we don't specify a default for OPENAI_API_KEY or include it in the config file
	// so we need to manually bind to the environment variable
	err := viper.BindEnv("OPENAI_API_KEY")
	if err != nil {
		log.Fatalf("Error binding environment variable: %s", err)
	}

	setLogLevel()
}

// loadEnv loads environment variables from .env file
func loadEnv() {
	err := godotenv.Load()
	if err != nil {
		log.Warn(".env file not found or unable to load")
	}
}

func setLogLevel() {
	var level logrus.Level

	if viper.IsSet("log.level") {
		switch viper.GetString("log.level") {
		case "debug":
			level = logrus.DebugLevel
		case "warn":
			level = logrus.WarnLevel
		case "error":
			level = logrus.ErrorLevel
		case "trace":
			level = logrus.TraceLevel
		}
		log.SetLevel(level)
	}
}

func setDefaults() {
	viper.SetDefault("embeddings.enable", true)
	viper.SetDefault("embeddings.dimensions", 1536)
	viper.SetDefault("embeddings.model", "AdaEmbeddingV2")
	viper.SetDefault("llm_model", "gpt-3.5-turbo0301")
	viper.SetDefault("messages.max_session_length", 500)
	viper.SetDefault("memory.message_window", 12)
	viper.SetDefault("memory.token_window", 500)
	viper.SetDefault("memory.summarize.enable", true)
	viper.SetDefault("memory.search.metric", "COSINE")
	viper.SetDefault("memory_store.type", "redis")
	viper.SetDefault("memory_store.url", "localhost:6379")
	viper.SetDefault("server.port", 8000)
}
