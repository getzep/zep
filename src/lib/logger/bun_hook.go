package logger

// this is for the most part a copy of the logrusbun hook - https://github.com/oiime/logrusbun

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/uptrace/bun"
)

const (
	maxQueryOpNameLen = 16
)

type QueryHookOptions struct {
	LogSlow    time.Duration
	QueryLevel LogLevel
	SlowLevel  LogLevel
	ErrorLevel LogLevel
}

type QueryHook struct {
	opts QueryHookOptions
}

type LogEntryVars struct {
	Timestamp time.Time
	Query     string
	Operation string
	Duration  time.Duration
	Error     error
}

// NewQueryHook returns new instance
func NewQueryHook(opts QueryHookOptions) *QueryHook {
	h := QueryHook{
		opts: opts,
	}

	return &h
}

func (*QueryHook) BeforeQuery(ctx context.Context, _ *bun.QueryEvent) context.Context {
	return ctx
}

func (h *QueryHook) AfterQuery(_ context.Context, event *bun.QueryEvent) {
	var level LogLevel

	now := time.Now()
	dur := now.Sub(event.StartTime)

	switch event.Err {
	case nil, sql.ErrNoRows:
		level = h.opts.QueryLevel

		if h.opts.LogSlow > 0 && dur >= h.opts.LogSlow {
			level = h.opts.SlowLevel
		}
	default:
		level = h.opts.ErrorLevel
	}

	if level == "" {
		return
	}

	msg := fmt.Sprintf("[%s]: %s", eventOperation(event), string(event.Query))

	fields := []any{
		"timestamp", now,
		"duration", dur,
	}

	if event.Err != nil {
		fields = append(fields, "error", event.Err)
	}

	switch level {
	case DebugLevel:
		Debug(msg, fields...)
	case InfoLevel:
		Info(msg, fields...)
	case WarnLevel:
		Warn(msg, fields...)
	case ErrorLevel:
		Error(msg, fields...)
	case FatalLevel:
		Fatal(msg, fields...)
	case PanicLevel:
		Panic(msg, fields...)
	default:
		panic(fmt.Errorf("unsupported level: %v", level))
	}
}

func eventOperation(event *bun.QueryEvent) string {
	switch event.IQuery.(type) {
	case *bun.SelectQuery:
		return "SELECT"
	case *bun.InsertQuery:
		return "INSERT"
	case *bun.UpdateQuery:
		return "UPDATE"
	case *bun.DeleteQuery:
		return "DELETE"
	case *bun.CreateTableQuery:
		return "CREATE TABLE"
	case *bun.DropTableQuery:
		return "DROP TABLE"
	}
	return queryOperation(event.Query)
}

func queryOperation(name string) string {
	if idx := strings.Index(name, " "); idx > 0 {
		name = name[:idx]
	}
	if len(name) > maxQueryOpNameLen {
		name = name[:maxQueryOpNameLen]
	}
	return string(name)
}
