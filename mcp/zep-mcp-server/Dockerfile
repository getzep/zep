# Multi-stage Dockerfile for Zep MCP Server
# Stage 1: Build
FROM golang:1.23.0-alpine3.19 AS builder

# Install build dependencies
RUN apk add --no-cache git ca-certificates tzdata

WORKDIR /build

# Copy go mod files first for better caching
COPY go.mod go.sum ./
RUN go mod download

# Copy source code
COPY . .

# Build static binary
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -ldflags="-s -w -X main.Version=${VERSION:-0.1.0}" \
    -o zep-mcp-server \
    ./cmd/server

# Stage 2: Runtime
FROM alpine:3.19

# Install runtime dependencies
RUN apk add --no-cache ca-certificates curl

# Create non-root user
RUN addgroup -g 1001 -S zep && \
    adduser -u 1001 -S zep -G zep

WORKDIR /app

# Copy binary from builder
COPY --from=builder /build/zep-mcp-server .

# Copy timezone data
COPY --from=builder /usr/share/zoneinfo /usr/share/zoneinfo

# Set ownership
RUN chown -R zep:zep /app

# Switch to non-root user
USER zep

# Health check for HTTP mode (port 8080)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Expose HTTP port
EXPOSE 8080

# Run the server (default to HTTP mode)
ENTRYPOINT ["/app/zep-mcp-server"]
