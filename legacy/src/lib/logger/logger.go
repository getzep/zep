package logger

import (
	"fmt"
	"strings"

	"github.com/ThreeDotsLabs/watermill"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"

	"github.com/getzep/zep/lib/config"
)

type LogFormat int

const (
	JsonFormat LogFormat = iota
	ConsoleFormat
)

type LogLevel string

func (ll LogLevel) String() string {
	return string(ll)
}

const (
	DebugLevel  LogLevel = "DEBUG"
	InfoLevel   LogLevel = "INFO"
	WarnLevel   LogLevel = "WARN"
	ErrorLevel  LogLevel = "ERROR"
	PanicLevel  LogLevel = "PANIC"
	DPanicLevel LogLevel = "DPANIC"
	FatalLevel  LogLevel = "FATAL"
)

// we use a singleton for the default logger. in the future we may expose an
// api for creating new instances for specific use cases.
var _instance *logger

func InitDefaultLogger() {
	if _instance != nil {
		return
	}

	var (
		lvl    = InfoLevel
		format = JsonFormat

		zapLevel  zap.AtomicLevel
		zapFormat string
	)

	if envLevel := config.Logger().Level; envLevel != "" {
		lvl = LogLevel(strings.ToUpper(envLevel))
	}

	if envFormat := config.Logger().Format; envFormat != "" {
		switch envFormat {
		case "json":
			format = JsonFormat
		case "console":
			format = ConsoleFormat
		default:
			// if we manage to get here, it's a bug and panicking is fine because
			// we'd want to prevent startup.
			panic(fmt.Errorf("bad log format in environment variable: %s", envFormat))
		}
	}

	switch lvl {
	case DebugLevel:
		zapLevel = zap.NewAtomicLevelAt(zap.DebugLevel)
	case InfoLevel:
		zapLevel = zap.NewAtomicLevelAt(zap.InfoLevel)
	case WarnLevel:
		zapLevel = zap.NewAtomicLevelAt(zap.WarnLevel)
	case ErrorLevel:
		zapLevel = zap.NewAtomicLevelAt(zap.ErrorLevel)
	case PanicLevel:
		zapLevel = zap.NewAtomicLevelAt(zap.PanicLevel)
	case DPanicLevel:
		zapLevel = zap.NewAtomicLevelAt(zap.DPanicLevel)
	case FatalLevel:
		zapLevel = zap.NewAtomicLevelAt(zap.FatalLevel)
	default:
		// if we manage to get here, it's a bug and panicking is fine because
		// we'd want to prevent startup.
		panic(fmt.Errorf("bad log level: %s", lvl))
	}

	switch format {
	case JsonFormat:
		zapFormat = "json"
	case ConsoleFormat:
		zapFormat = "console"
	default:
		panic(fmt.Errorf("bad log format: %d", format))
	}

	zapConfig := zap.Config{
		Level:            zapLevel,
		Development:      false,
		Encoding:         zapFormat,
		OutputPaths:      []string{"stdout"},
		ErrorOutputPaths: []string{"stdout"},
		DisableCaller:    false,
		EncoderConfig: zapcore.EncoderConfig{
			MessageKey:     "msg",
			LevelKey:       "level",
			TimeKey:        "ts",
			StacktraceKey:  "stack",
			LineEnding:     zapcore.DefaultLineEnding,
			EncodeLevel:    zapcore.CapitalLevelEncoder,
			EncodeTime:     zapcore.ISO8601TimeEncoder,
			EncodeDuration: zapcore.StringDurationEncoder,
			EncodeCaller:   zapcore.ShortCallerEncoder,
		},
	}

	log, err := zapConfig.Build()
	if err != nil {
		panic(err)
	}

	l := &logger{
		level:  lvl,
		format: format,

		logger: log.Sugar(),
	}

	_instance = l
}

func GetLogLevel() LogLevel {
	return _instance.level
}

// Use only if absolutely needed
func GetZapLogger() *zap.Logger {
	return _instance.logger.Desugar()
}

func GetLogger() Logger {
	return _instance
}

type Logger interface {
	Debug(msg string, keysAndValues ...any)
	Info(msg string, keysAndValues ...any)
	Warn(msg string, keysAndValues ...any)
	Error(msg string, keysAndValues ...any)
	Panic(msg string, keysAndValues ...any)
	DPanic(msg string, keysAndValues ...any)
	Fatal(msg string, keysAndValues ...any)
}

type logger struct {
	level  LogLevel
	format LogFormat

	logger *zap.SugaredLogger
}

func (l logger) Debug(msg string, keysAndValues ...any) {
	l.logger.Debugw(msg, keysAndValues...)
}

func (l logger) Info(msg string, keysAndValues ...any) {
	l.logger.Infow(msg, keysAndValues...)
}

func (l logger) Warn(msg string, keysAndValues ...any) {
	l.logger.Warnw(msg, keysAndValues...)
}

func (l logger) Error(msg string, keysAndValues ...any) {
	l.logger.Errorw(msg, keysAndValues...)
}

func (l logger) Panic(msg string, keysAndValues ...any) {
	l.logger.Panicw(msg, keysAndValues...)
}

func (l logger) DPanic(msg string, keysAndValues ...any) {
	l.logger.DPanicw(msg, keysAndValues...)
}

func (l logger) Fatal(msg string, keysAndValues ...any) {
	l.logger.Fatalw(msg, keysAndValues...)
}

func Debug(msg string, keysAndValues ...any) {
	_instance.Debug(msg, keysAndValues...)
}

func Info(msg string, keysAndValues ...any) {
	_instance.Info(msg, keysAndValues...)
}

func Warn(msg string, keysAndValues ...any) {
	_instance.Warn(msg, keysAndValues...)
}

func Error(msg string, keysAndValues ...any) {
	_instance.Error(msg, keysAndValues...)
}

func Panic(msg string, keysAndValues ...any) {
	_instance.Panic(msg, keysAndValues...)
}

func DPanic(msg string, keysAndValues ...any) {
	_instance.DPanic(msg, keysAndValues...)
}

func Fatal(msg string, keysAndValues ...any) {
	_instance.Fatal(msg, keysAndValues...)
}

type watermillLogger struct {
	fields watermill.LogFields
}

func GetWatermillLogger() watermill.LoggerAdapter {
	return &watermillLogger{}
}

func (l *watermillLogger) Error(msg string, err error, fields watermill.LogFields) {
	fields = l.fields.Add(fields)

	keysAndValues := make([]any, 0, len(fields)+1)

	for k, v := range fields {
		keysAndValues = append(keysAndValues, k, v)
	}

	keysAndValues = append(keysAndValues, "error", err)

	_instance.Error(msg, keysAndValues...)
}

func (l *watermillLogger) Info(msg string, fields watermill.LogFields) {
	fields = l.fields.Add(fields)

	keysAndValues := make([]any, 0, len(fields))

	for k, v := range fields {
		keysAndValues = append(keysAndValues, k, v)
	}

	_instance.Info(msg, keysAndValues...)
}

func (l *watermillLogger) Debug(msg string, fields watermill.LogFields) {
	fields = l.fields.Add(fields)

	keysAndValues := make([]any, 0, len(fields))

	for k, v := range fields {
		keysAndValues = append(keysAndValues, k, v)
	}

	_instance.Debug(msg, keysAndValues...)
}

func (l *watermillLogger) Trace(msg string, fields watermill.LogFields) {
	fields = l.fields.Add(fields)

	keysAndValues := make([]any, 0, len(fields))

	for k, v := range fields {
		keysAndValues = append(keysAndValues, k, v)
	}

	_instance.Debug(msg, keysAndValues...)
}

func (l *watermillLogger) With(fields watermill.LogFields) watermill.LoggerAdapter {
	return &watermillLogger{
		fields: l.fields.Add(fields),
	}
}
