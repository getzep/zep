package cmd

import (
	"os"

	"github.com/danielchalef/zep/internal"
	"github.com/sirupsen/logrus"

	"github.com/spf13/cobra"
)

var (
	log *logrus.Logger

	cfgFile string
)

var cmd = &cobra.Command{
	Use:   "zep",
	Short: "zep stores, manages, enriches, and searches long-term memory for conversational AI applications",
	Run:   func(cmd *cobra.Command, args []string) { run() },
}

func init() {
	cmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file (default config.yaml)")
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
