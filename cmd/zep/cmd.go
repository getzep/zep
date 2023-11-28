package cmd

import (
	"context"
	"fmt"
	"os"

	"github.com/getzep/zep/config"
	"github.com/getzep/zep/internal"
	"github.com/getzep/zep/pkg/models"
	"github.com/getzep/zep/pkg/store/postgres"
	"github.com/sirupsen/logrus"

	"github.com/spf13/cobra"
)

var (
	log *logrus.Logger

	cfgFile     string
	showVersion bool
	dumpConfig  bool
	generateKey bool
	fixturePath string
)

var cmd = &cobra.Command{
	Use:   "zep",
	Short: "zep stores, manages, enriches, and searches long-term memory for conversational AI applications",
	Run:   func(cmd *cobra.Command, args []string) { run() },
}

var testCmd = &cobra.Command{
	Use:   "test",
	Short: "Test utilities",
}

var createFixturesCmd = &cobra.Command{
	Use:   "create-fixtures",
	Short: "Create fixtures for testing",
	Run: func(cmd *cobra.Command, args []string) {
		fixtureCount, _ := cmd.Flags().GetInt("count")
		outputDir, _ := cmd.Flags().GetString("outputDir")
		postgres.GenerateFixtureData(fixtureCount, outputDir)
		fmt.Println("Fixtures created successfully.")
	},
}

var loadFixturesCmd = &cobra.Command{
	Use:   "load-fixtures",
	Short: "Load fixtures for testing",
	Run: func(cmd *cobra.Command, args []string) {
		cfg, err := config.LoadConfig(cfgFile)
		if err != nil {
			log.Fatalf("Error configuring Zep: %s", err)
		}
		appState := &models.AppState{
			Config: cfg,
		}
		db, err := postgres.NewPostgresConn(appState)
		if err != nil {
			log.Fatalf("Failed to connect to database: %v\n", err)
		}
		err = postgres.LoadFixtures(context.Background(), appState, db, fixturePath)
		if err != nil {
			log.Fatalf("Failed to load fixtures: %v\n", err)
		}
		fmt.Println("Fixtures loaded successfully.")
	},
}

var dumpJsonSchemaCmd = &cobra.Command{
	Use:     "json-schema",
	Short:   "Generates JSON Schema for Zep's configuration file",
	Example: "zep json-schema > zep_config_schema.json",
	RunE: func(cmd *cobra.Command, args []string) error {
		schema, err := config.JSONSchema()
		if err != nil {
			return err
		}
		fmt.Println(string(schema))
		return nil
	},
}

func init() {
	testCmd.AddCommand(createFixturesCmd)
	testCmd.AddCommand(loadFixturesCmd)
	cmd.AddCommand(testCmd)
	cmd.AddCommand(dumpJsonSchemaCmd)

	cmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file (default config.yaml)")
	cmd.PersistentFlags().BoolVarP(&showVersion, "version", "v", false, "print version number")
	cmd.PersistentFlags().BoolVarP(&dumpConfig, "dump-config", "d", false, "dump config")
	cmd.PersistentFlags().
		BoolVarP(&generateKey, "generate-token", "g", false, "generate a new JWT token")

	createFixturesCmd.Flags().Int("count", 100, "Number of fixtures to generate per model")
	createFixturesCmd.Flags().String("outputDir", "./test_data", "Path to output fixtures")
	loadFixturesCmd.Flags().
		StringVarP(&fixturePath, "fixturePath", "f", "./test_data", "Path containing fixtures to load")
}

// Execute executes the root cobra command.
func Execute() {
	log = internal.GetLogger()
	log.SetLevel(logrus.InfoLevel)

	err := cmd.Execute()

	if err != nil {
		os.Exit(1)
	}
}
