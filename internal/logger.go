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
			PadLevelText:  true,
		})
	})

	return logger
}

func SetLogLevel(level logrus.Level) {
	logger.SetLevel(level)
}

// LeveledLogger is an interface that wraps the logrus Logger interface
type LeveledLogger interface {
	Error(msg string, keysAndValues ...interface{})
	Info(msg string, keysAndValues ...interface{})
	Debug(msg string, keysAndValues ...interface{})
	Warn(msg string, keysAndValues ...interface{})
}

var _ LeveledLogger = &LeveledLogrus{}

// NewLeveledLogrus returns a new LeveledLogrus instance. This is a wrapper
// around logrus.Logger that implements the LeveledLogger interface.
// We use this for the retyrablehttp client.
func NewLeveledLogrus(logger *logrus.Logger) *LeveledLogrus {
	return &LeveledLogrus{
		Logger: logger,
	}
}

type LeveledLogrus struct {
	*logrus.Logger
}

func (l *LeveledLogrus) fields(keysAndValues ...interface{}) map[string]interface{} {
	fields := make(map[string]interface{})

	for i := 0; i < len(keysAndValues)-1; i += 2 {
		if key, ok := keysAndValues[i].(string); ok {
			fields[key] = keysAndValues[i+1]
		}
	}

	return fields
}

func (l *LeveledLogrus) Error(msg string, keysAndValues ...interface{}) {
	l.WithFields(l.fields(keysAndValues...)).Error(msg)
}

func (l *LeveledLogrus) Info(msg string, keysAndValues ...interface{}) {
	l.WithFields(l.fields(keysAndValues...)).Info(msg)
}

func (l *LeveledLogrus) Warn(msg string, keysAndValues ...interface{}) {
	l.WithFields(l.fields(keysAndValues...)).Warn(msg)
}

func (l *LeveledLogrus) Debug(msg string, keysAndValues ...interface{}) {
	l.WithFields(l.fields(keysAndValues...)).Debug(msg)
}
