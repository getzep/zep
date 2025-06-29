package main

import (
	"context"
	"errors"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"github.com/getzep/zep/api"
	"github.com/getzep/zep/lib/config"
	"github.com/getzep/zep/lib/logger"
	"github.com/getzep/zep/lib/observability"
	"github.com/getzep/zep/lib/telemetry"
	"github.com/getzep/zep/models"
)

func main() {
	config.Load()

	logger.InitDefaultLogger()

	as := newAppState()

	srv, err := api.Create(as)
	if err != nil {
		logger.Panic("Failed to create server", "error", err)
	}

	done := setupSignalHandler(as, srv)

	err = srv.ListenAndServe()
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		logger.Panic("Failed to start server", "error", err)
	}

	<-done
}

func setupSignalHandler(as *models.AppState, srv *http.Server) chan struct{} {
	done := make(chan struct{}, 1)

	signalCh := make(chan os.Signal, 1)
	signal.Notify(signalCh, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		<-signalCh

		// the order of these calls is important and intentional
		// shutting down the server and task router first stops all work
		// then we shut down ancillary services
		// then we close database connections
		// finally close observability. this is last to ensure we can capture
		// any errors that occurred during shutdown.

		// ignoring the error here because we're going to shutdown anyways.
		// the error here is irrelevant as it is not actionable and very unlikely to
		// happen.
		srv.Shutdown(context.Background())

		if err := as.TaskRouter.Close(); err != nil {
			logger.Error("Error closing task router", "error", err)
		}

		telemetry.Shutdown()

		gracefulShutdown()

		if err := as.DB.Close(); err != nil {
			logger.Error("Error closing database connection", "error", err)
		}

		observability.Shutdown()

		done <- struct{}{}
	}()

	return done
}
