package server

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/getzep/zep/mcp/zep-mcp-server/internal/config"
	"github.com/getzep/zep/mcp/zep-mcp-server/internal/handlers"
	zepclient "github.com/getzep/zep/mcp/zep-mcp-server/pkg/zep"
)

// Server represents the Zep MCP server
type Server struct {
	config    *config.Config
	mcp       *mcp.Server
	zepClient *zepclient.Client
	logger    *slog.Logger
}

// New creates a new Zep MCP server
func New(cfg *config.Config) (*Server, error) {
	// Initialize logger
	logLevel := slog.LevelInfo
	switch cfg.LogLevel {
	case "debug":
		logLevel = slog.LevelDebug
	case "warn":
		logLevel = slog.LevelWarn
	case "error":
		logLevel = slog.LevelError
	}

	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{
		Level: logLevel,
	}))

	// Create Zep client
	zepClient := zepclient.NewClient(cfg.ZepAPIKey)

	// Create MCP server
	impl := &mcp.Implementation{
		Name:    cfg.ServerName,
		Version: cfg.ServerVersion,
	}

	mcpServer := mcp.NewServer(impl, nil)

	server := &Server{
		config:    cfg,
		mcp:       mcpServer,
		zepClient: zepClient,
		logger:    logger,
	}

	// Register tools
	server.registerTools()

	return server, nil
}

// registerTools registers all MCP tools with their handlers
func (s *Server) registerTools() {
	s.logger.Info("Registering MCP tools")

	// Phase 1: Core search and retrieval tools
	mcp.AddTool[handlers.SearchGraphInput, any](s.mcp, SearchGraphTool, handlers.HandleSearchGraph(s.zepClient))
	mcp.AddTool[handlers.GetUserContextInput, any](s.mcp, GetUserContextTool, handlers.HandleGetUserContext(s.zepClient))
	mcp.AddTool[handlers.GetUserInput, any](s.mcp, GetUserTool, handlers.HandleGetUser(s.zepClient))
	mcp.AddTool[handlers.ListThreadsInput, any](s.mcp, ListThreadsTool, handlers.HandleListThreads(s.zepClient))

	// Phase 2: Advanced graph query tools
	mcp.AddTool[handlers.GetUserNodesInput, any](s.mcp, GetUserNodesTool, handlers.HandleGetUserNodes(s.zepClient))
	mcp.AddTool[handlers.GetUserEdgesInput, any](s.mcp, GetUserEdgesTool, handlers.HandleGetUserEdges(s.zepClient))
	mcp.AddTool[handlers.GetEpisodesInput, any](s.mcp, GetEpisodesTool, handlers.HandleGetEpisodes(s.zepClient))

	s.logger.Info("Registered 7 tools")
}

// Run starts the MCP server
func (s *Server) Run(ctx context.Context) error {
	if s.config.TransportMode == "stdio" {
		// Stdio transport for Claude Desktop, Cline, etc.
		return s.runStdio(ctx)
	}
	// HTTP transport (default)
	return s.runHTTP(ctx)
}

// runStdio runs the server with stdio transport
func (s *Server) runStdio(ctx context.Context) error {
	s.logger.Info("Starting Zep MCP Server with stdio transport",
		"name", s.config.ServerName,
		"version", s.config.ServerVersion,
	)

	transport := &mcp.StdioTransport{}
	if err := s.mcp.Run(ctx, transport); err != nil {
		s.logger.Error("Server error", "error", err)
		return err
	}

	return nil
}

// runHTTP runs the server with MCP Streamable HTTP Transport
// See: https://modelcontextprotocol.io/specification/2025-03-26/streamable-http-transport.html
func (s *Server) runHTTP(ctx context.Context) error {
	s.logger.Info("Starting Zep MCP Server with HTTP transport",
		"name", s.config.ServerName,
		"version", s.config.ServerVersion,
		"port", s.config.HTTPPort,
	)

	// Create HTTP handler using MCP SDK's built-in streamable handler
	// This implements the MCP Streamable HTTP Transport (2025-03-26 spec)
	// Supports both stateless JSON requests and streaming with text/event-stream
	handler := mcp.NewStreamableHTTPHandler(
		func(r *http.Request) *mcp.Server {
			return s.mcp
		},
		nil, // Use default options
	)

	// Set up HTTP server
	httpServer := &http.Server{
		Addr:    fmt.Sprintf(":%d", s.config.HTTPPort),
		Handler: handler,
	}

	s.logger.Info("HTTP server listening",
		"url", fmt.Sprintf("http://localhost:%d", s.config.HTTPPort),
		"transport", "MCP Streamable HTTP",
	)

	// Run server in background
	errCh := make(chan error, 1)
	go func() {
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			errCh <- err
		}
	}()

	// Wait for context cancellation or error
	select {
	case <-ctx.Done():
		s.logger.Info("Shutting down HTTP server...")
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5)
		defer cancel()
		if err := httpServer.Shutdown(shutdownCtx); err != nil {
			s.logger.Error("HTTP server shutdown error", "error", err)
			return err
		}
		return nil
	case err := <-errCh:
		s.logger.Error("HTTP server error", "error", err)
		return err
	}
}
