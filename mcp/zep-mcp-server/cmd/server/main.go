package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/config"
	"github.com/getzep/zep/mcp/zep-mcp-server/internal/server"
)

func main() {
	// Parse command-line flags
	useStdio := flag.Bool("stdio", false, "Use stdio transport instead of HTTP (for Claude Desktop, Cline, etc.)")
	httpPort := flag.Int("port", 8080, "HTTP port to listen on (when not using stdio)")
	flag.Parse()

	// Load configuration
	cfg, err := config.LoadConfig()
	if err != nil {
		fmt.Fprintf(os.Stderr, "Configuration error: %v\n", err)
		fmt.Fprintf(os.Stderr, "\nPlease set ZEP_API_KEY environment variable or create a .env file.\n")
		fmt.Fprintf(os.Stderr, "See .env.example for an example configuration.\n")
		os.Exit(1)
	}

	// Set transport mode
	if *useStdio {
		cfg.TransportMode = "stdio"
	} else {
		cfg.TransportMode = "http"
		cfg.HTTPPort = *httpPort
	}

	// Create server
	srv, err := server.New(cfg)
	if err != nil {
		log.Fatalf("Failed to create server: %v", err)
	}

	// Set up context with cancellation
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Handle shutdown signals
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)

	go func() {
		<-sigCh
		fmt.Fprintf(os.Stderr, "\nShutting down gracefully...\n")
		cancel()
	}()

	// Run server
	if err := srv.Run(ctx); err != nil {
		log.Fatalf("Server error: %v", err)
	}
}
