package internal

import (
	"os"
	"sync"

	"github.com/sirupsen/logrus"
)

var once sync.Once
var logger *logrus.Logger

// GetLogger returns a singleton logger correctly configured for zep
func GetLogger() *logrus.Logger {
	// Use a singleton so we can update log level once config is loaded
	once.Do(func() {
		logger = logrus.New()

		logger.Out = os.Stdout
		logger.SetLevel(logrus.WarnLevel)

		logger.SetFormatter(&logrus.TextFormatter{
			DisableColors: false,
			FullTimestamp: true,
		})
	})

	return logger
}

func SetLogLevel(level logrus.Level) {
	logger.SetLevel(level)
}
