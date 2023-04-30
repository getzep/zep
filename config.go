package main

import (
	"log"

	"github.com/joho/godotenv"
	"github.com/spf13/cobra"
	"github.com/spf13/viper"
)

func initCobraFlags(cmd *cobra.Command) {
	cmd.Flags().StringP("config", "c", "", "Path to the configuration file")
	cmd.Flags().BoolP("long-term-memory", "l", false, "Enable long-term memory using RediSearch")
	cmd.Flags().IntP("port", "p", 8000, "Port for the server to listen on")

	err := viper.BindPFlag("LONG_TERM_MEMORY", cmd.Flags().Lookup("long-term-memory"))
	if err != nil {
		log.Fatalf("Error binding long-term-memory flag: %v", err)
	}
	err = viper.BindPFlag("PORT", cmd.Flags().Lookup("port"))
	if err != nil {
		log.Fatalf("Error binding port flag: %v", err)
	}
}

func initConfig(cmd *cobra.Command) {
	configFile, err := cmd.Flags().GetString("config")
	if err != nil {
		log.Fatalf("Error reading config flag: %v", err)
	}

	if configFile != "" {
		// If the -c flag is provided, use the specified config file.
		viper.SetConfigFile(configFile)
		err := viper.ReadInConfig()
		if err != nil {
			log.Fatalf("Error reading config file: %v", err)
		}
		log.Println("Using config file:", viper.ConfigFileUsed())
	} else {
		// If the -c flag is not provided, look for a config.yaml file in the current directory.
		viper.SetConfigName("config")
		viper.SetConfigType("yaml")
		viper.AddConfigPath(".")

		err := viper.ReadInConfig()
		if err != nil {
			log.Println("No config.yaml file found in the current directory. Using default values and environment variables.")
		} else {
			log.Println("Using config file:", viper.ConfigFileUsed())
		}
	}
}

func loadEnv() {
	err := godotenv.Load()
	if err != nil {
		log.Printf("Warning: .env file not found or unable to load")
	}
}

func configureViper() {
	viper.SetEnvPrefix("PAPYRUS")
	viper.AutomaticEnv()
	viper.SetDefault("PORT", 8000)
	viper.SetDefault("MAX_WINDOW_SIZE", 12)
}
