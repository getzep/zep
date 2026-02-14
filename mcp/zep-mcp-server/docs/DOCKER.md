# Docker Deployment Guide

This guide covers deploying the Zep MCP Server using Docker and Docker Compose.

## Quick Start

1. **Create environment file:**
   ```bash
   cp .env.example .env
   # Edit .env and add your ZEP_API_KEY
   ```

2. **Start with Docker Compose:**
   ```bash
   docker-compose up
   ```

The server will be available at `http://localhost:8080`.

## Docker Build

### Building the Image

Build the Docker image manually:

```bash
docker build -t zep-mcp-server:latest .
```

Or use the build script:

```bash
./scripts/build.sh
```

### Build Arguments

- `VERSION`: Set the version string (default: 0.1.0)

```bash
docker build --build-arg VERSION=1.0.0 -t zep-mcp-server:1.0.0 .
```

### Multi-Platform Builds

Build for multiple platforms:

```bash
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    -t zep-mcp-server:latest \
    .
```

## Running with Docker

### Run with Environment Variables

```bash
docker run -d \
    --name zep-mcp-server \
    -e ZEP_API_KEY=your-key-here \
    -p 8080:8080 \
    zep-mcp-server:latest
```

### Run with Environment File

```bash
docker run -d \
    --name zep-mcp-server \
    --env-file .env \
    -p 8080:8080 \
    zep-mcp-server:latest
```

### Run with Custom Port

```bash
docker run -d \
    --name zep-mcp-server \
    -e ZEP_API_KEY=your-key-here \
    -p 9000:8080 \
    zep-mcp-server:latest \
    --port 8080
```

### Run in Stdio Mode

For use with Claude Desktop or Cline:

```bash
docker run -i \
    --name zep-mcp-server \
    -e ZEP_API_KEY=your-key-here \
    zep-mcp-server:latest \
    --stdio
```

## Docker Compose

### Starting the Server

Start in background:
```bash
docker-compose up -d
```

Start with logs:
```bash
docker-compose up
```

### Stopping the Server

```bash
docker-compose down
```

### Viewing Logs

```bash
docker-compose logs -f
```

### Checking Status

```bash
docker-compose ps
```

### Restarting the Server

```bash
docker-compose restart
```

## Configuration

### Environment Variables

Set these in `.env` file or pass via `-e` flag:

- **ZEP_API_KEY** (required): Your Zep Cloud API key
- **LOG_LEVEL** (optional): Logging level (debug, info, warn, error) - default: info

### docker-compose.yml

The compose file includes:

- **Health checks**: Automatic health monitoring
- **Resource limits**: CPU and memory constraints
- **Security**: Non-root user, read-only filesystem, no new privileges
- **Logging**: Configured with rotation (10MB max, 3 files)
- **Restart policy**: Automatically restarts unless stopped manually

### Customizing docker-compose.yml

Edit resource limits:

```yaml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 256M
```

Change port mapping:

```yaml
ports:
  - "9000:8080"
```

## Health Checks

The Docker container includes health checks that verify the server is responding on port 8080.

Check health status:
```bash
docker inspect --format='{{.State.Health.Status}}' zep-mcp-server
```

View health check logs:
```bash
docker inspect --format='{{range .State.Health.Log}}{{.Output}}{{end}}' zep-mcp-server
```

## Security

### Non-Root User

The container runs as user `zep` (UID 1001) for security.

### Read-Only Filesystem

The container uses a read-only root filesystem with only `/tmp` as writable.

### Security Options

- `no-new-privileges`: Prevents privilege escalation
- Minimal Alpine base image with only essential packages

## Troubleshooting

### Container Won't Start

Check logs:
```bash
docker-compose logs
```

Verify environment variables:
```bash
docker-compose config
```

### Connection Issues

Verify port mapping:
```bash
docker ps
```

Test connectivity:
```bash
curl http://localhost:8080
```

### Health Check Failures

Check health status:
```bash
docker inspect zep-mcp-server | grep -A 10 Health
```

### Permission Issues

Ensure the container can write to /tmp:
```bash
docker exec zep-mcp-server ls -la /tmp
```

## Production Deployment

### Using Docker Compose

The included `docker-compose.yml` is configured for production with:

- Automatic restarts
- Resource limits
- Health checks
- Security hardening
- Log rotation

### Best Practices

1. **Use secrets management** for ZEP_API_KEY:
   - Docker secrets
   - Kubernetes secrets
   - Cloud provider secret managers

2. **Monitor resource usage**:
   ```bash
   docker stats zep-mcp-server
   ```

3. **Set up log aggregation**:
   - Configure Docker logging driver for your platform
   - Use centralized logging (CloudWatch, Datadog, etc.)

4. **Enable automatic updates**:
   - Use Watchtower or similar tools
   - Set up CI/CD pipeline

5. **Use specific version tags**:
   ```bash
   docker build -t zep-mcp-server:1.0.0 .
   ```

## Makefile Integration

Use make commands for common Docker operations:

```bash
# Build Docker image
make docker-build

# Run with Docker Compose
make docker-run

# Stop Docker Compose
make docker-down

# Clean Docker resources
make docker-clean
```

## Registry Publishing

### Tag for Registry

```bash
docker tag zep-mcp-server:latest your-registry.com/zep-mcp-server:latest
```

### Push to Registry

```bash
docker push your-registry.com/zep-mcp-server:latest
```

### Pull from Registry

```bash
docker pull your-registry.com/zep-mcp-server:latest
```

## Advanced Usage

### Custom Entrypoint

Override the entrypoint for debugging:

```bash
docker run -it --entrypoint /bin/sh zep-mcp-server:latest
```

### Volume Mounting

Mount configuration files:

```bash
docker run -v $(pwd)/.env:/app/.env zep-mcp-server:latest
```

### Network Configuration

Use custom networks:

```bash
docker network create zep-network
docker run --network zep-network zep-mcp-server:latest
```

## CI/CD Integration

### GitHub Actions Example

```yaml
- name: Build Docker image
  run: docker build -t zep-mcp-server:${{ github.sha }} .

- name: Run tests
  run: docker run zep-mcp-server:${{ github.sha }} make test

- name: Push to registry
  run: |
    docker tag zep-mcp-server:${{ github.sha }} registry/zep-mcp-server:latest
    docker push registry/zep-mcp-server:latest
```

## Support

For issues or questions:
- Check the main [README.md](../README.md)
- Review [TOOLS.md](TOOLS.md) for API documentation
- Open an issue on GitHub
