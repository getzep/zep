package cmd

import (
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

func Execute() {
	err := cmd.Execute()

	if err != nil {
		os.Exit(1)
	}
}

func init() {
	cobra.OnInitialize(initConfig)

	cmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file (default config.yaml)")
}

func initConfig() {
	loadEnv()

	if cfgFile != "" {
		// Use config file from the flag.
		viper.SetConfigFile(cfgFile)
	} else {
		viper.AddConfigPath(".")
		viper.SetConfigType("yaml")
		viper.SetConfigName("config")
	}

	viper.SetEnvPrefix(
		"ZEP",
	)
	viper.SetEnvKeyReplacer(strings.NewReplacer(`.`, `_`)) // replaced nested . with _
	setDefaults()
	viper.AutomaticEnv()

	if err := viper.ReadInConfig(); err != nil {
		if _, ok := err.(viper.ConfigFileNotFoundError); ok {
			if cfgFile == "" {
				return
			}
			// Only fail if a config file was specified and not found
			log.Fatalf("Config file %s not found", cfgFile)
		}
		log.Fatalf("Error reading config file: %s", err)
	}
}

func loadEnv() {
	err := godotenv.Load()
	if err != nil {
		log.Warn(".env file not found or unable to load")
	}
}

func setDefaults() {
	viper.SetDefault("PORT", 8000)
	viper.SetDefault("MAX_WINDOW_SIZE", 12)
}
