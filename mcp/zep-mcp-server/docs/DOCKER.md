# Docker Deployment Guide

This package includes a minimal Docker image and a minimal Compose setup.

## Files

- `Dockerfile`: multi-stage build that produces a small non-root runtime image
- `docker-compose.yml`: runs the server and loads environment variables from `.env`
- `.env.example`: template for the required runtime variables

## Quick Start

1. Create the environment file:

```bash
cp .env.example .env
```

2. Set `ZEP_API_KEY` in `.env`.
   Change `ZEP_MCP_HOST_PORT` in `.env` if you do not want the default host port `8081`.

3. Start the server:

```bash
docker compose up --build
```

By default Docker publishes the server on `http://localhost:8081`.

## Compose Environment Loading

`docker-compose.yml` uses:

```yaml
env_file:
  - .env
```

That means `docker compose` loads `ZEP_API_KEY` and `LOG_LEVEL` from the local `.env` file and passes them into the container environment. The same `.env` file also provides `ZEP_MCP_HOST_PORT` for the host-side port mapping.

## Build and Run Manually

Build the image:

```bash
docker build -t zep-mcp-server:latest .
```

Run it with the same `.env` file:

```bash
# If you changed ZEP_MCP_HOST_PORT in .env, use the same host port here
docker run --rm \
  --env-file .env \
  -p 8081:8080 \
  zep-mcp-server:latest
```

## Common Commands

Start in the background:

```bash
docker compose up -d --build
```

Stop the service:

```bash
docker compose down
```

Follow logs:

```bash
docker compose logs -f
```

Render the resolved Compose config:

```bash
docker compose config
```

## Notes

- The server inside the container still listens on port `8080`.
- Docker publishes that container port on host `8081` by default.
- The application itself still supports reading a `.env` file when run directly outside Docker.
- For Docker, the standard path is `env_file: .env` in Compose or `--env-file .env` with `docker run`.
