package internal

import (
	"os"
	"sync"

	"github.com/sirupsen/logrus"
	"github.com/spf13/viper"
)

var once sync.Once
var logger *logrus.Logger

func GetLogger() *logrus.Logger {
	var level logrus.Level

	level = logrus.InfoLevel

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
	}

	// Use a singleton so we can update log level once config is loaded
	once.Do(func() {
		logger = logrus.New()
	})

	logger.Out = os.Stdout
	logger.SetLevel(level)

	logger.SetFormatter(&logrus.TextFormatter{
		DisableColors: false,
		FullTimestamp: true,
	})

	return logger
}
