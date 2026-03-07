# Zep MCP Server

A Model Context Protocol (MCP) server for [Zep Cloud](https://www.getzep.com/), providing read-only access to Zep's temporal knowledge graph and memory features.

## Features

- **🔍 Search & Retrieval**: Search the knowledge graph, detect structural patterns, retrieve context, and access conversation history
- **📊 Graph Exploration**: Query nodes, edges, and episodes from the temporal knowledge graph
- **🔒 Read-Only**: Safe, non-destructive operations for AI assistants
- **⚡ Fast**: Built with Go for optimal performance
- **🎯 MCP-Compatible**: Works with Claude Desktop, Cline, and other MCP clients

## Tools

The server provides 14 read-only tools:

### Core Search & Retrieval

1. **`search_graph`** - Search the knowledge graph with filters, reranking, and scoped search
2. **`detect_patterns`** - Detect recurring structural patterns in a user or named graph
3. **`get_user_context`** - Retrieve formatted context for a thread (supports custom templates)
4. **`get_user`** - Get user information and metadata
5. **`list_threads`** - List conversation threads for a user

### Graph Query

6. **`get_user_nodes`** - Retrieve entity nodes from a user's knowledge graph
7. **`get_user_edges`** - Retrieve relationship edges from a user's knowledge graph
8. **`get_episodes`** - Get episode nodes (temporal data ingestion events)

### Detail Retrieval

9. **`get_thread_messages`** - Retrieve messages from a conversation thread
10. **`get_node`** - Get a specific node by UUID
11. **`get_edge`** - Get a specific edge by UUID
12. **`get_episode`** - Get a specific episode by UUID
13. **`get_node_edges`** - Get all edges connected to a specific node
14. **`get_episode_mentions`** - Get nodes and edges mentioned in an episode

## Installation

### Prerequisites

- Go 1.21 or later
- A Zep Cloud account with API key ([sign up](https://app.getzep.com))

### From Source

```bash
# Clone the repository
git clone https://github.com/getzep/zep.git
cd zep/mcp/zep-mcp-server

# Build the server
go build -o zep-mcp-server cmd/server/main.go

# Or install directly
go install github.com/getzep/zep/mcp/zep-mcp-server/cmd/server@latest
```

## Configuration

### Environment Variables

The server requires a Zep Cloud API key. You can provide it via environment variable or `.env` file.

**Required:**
- `ZEP_API_KEY` - Your Zep Cloud API key

**Optional:**
- `LOG_LEVEL` - Logging level: `debug`, `info`, `warn`, `error` (default: `info`)

### Using .env File

1. Copy the example configuration:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your API key:
   ```bash
   ZEP_API_KEY=your-zep-api-key-here
   LOG_LEVEL=info
   ```

3. Run the server:
   ```bash
   ./zep-mcp-server
   ```

### Using Environment Variables

```bash
export ZEP_API_KEY=your-zep-api-key-here
./zep-mcp-server
```

## Usage

### Running the Server

The server supports two transport modes:

**HTTP Mode (default):**
```bash
./zep-mcp-server
# Or specify a custom port
./zep-mcp-server --port 9000
```

**Stdio Mode (for Claude Desktop, Cline):**
```bash
./zep-mcp-server --stdio
```

### Docker

See [Docker Deployment Guide](docs/DOCKER.md) for full Docker documentation.

**Quick Start:**
```bash
# Build the image
make docker-build

# Run with docker compose (loads .env)
make docker-run
```

Or manually:
```bash
cp .env.example .env
docker build -t zep-mcp-server:latest .
docker run --env-file .env -p 8080:8080 zep-mcp-server:latest
```

### MCP Client Configuration

#### Claude Desktop

Add to your Claude Desktop configuration (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "zep": {
      "command": "/path/to/zep-mcp-server",
      "args": ["--stdio"],
      "env": {
        "ZEP_API_KEY": "your-zep-api-key-here"
      }
    }
  }
}
```

#### Cline (VS Code Extension)

Add to your Cline MCP settings (`.cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "zep": {
      "command": "/path/to/zep-mcp-server",
      "args": ["--stdio"],
      "env": {
        "ZEP_API_KEY": "your-zep-api-key-here"
      }
    }
  }
}
```

#### Claude Code (HTTP)

Claude Code uses HTTP transport to connect to MCP servers. Configure it using the `claude mcp add` CLI command.

1. Start the server in HTTP mode (default):
   ```bash
   export ZEP_API_KEY=your-zep-api-key-here
   ./zep-mcp-server
   # Server starts on http://localhost:8080
   ```

2. Add the server to Claude Code:
   ```bash
   claude mcp add --transport http zep http://localhost:8080
   ```

3. Verify the server is available:
   ```bash
   claude mcp list
   ```

**Using Docker:**
```bash
# Edit .env file with your ZEP_API_KEY
docker compose up -d

# Add to Claude Code
claude mcp add --transport http zep http://localhost:8080
```

The HTTP transport supports both stateless JSON requests and streaming responses, allowing Claude Code and other HTTP-based MCP clients to interact with the Zep knowledge graph.

## Tool Usage Examples

### Search the Knowledge Graph

```javascript
// Search for facts about a user
tools.search_graph({
  user_id: "user_123",
  query: "What are their preferences?",
  scope: "edges",  // edges, nodes, or episodes
  limit: 10
})
```

### Detect Graph Patterns

```javascript
// Detect recurring graph patterns around decision nodes
tools.detect_patterns({
  user_id: "user_123",
  seeds: {
    node_labels: ["Decision"]
  },
  recency_weight: "30_days",
  include_examples: true
})
```

### Get Thread Context

```javascript
// Retrieve context for a conversation
tools.get_user_context({
  thread_id: "thread_456",
  template_id: "my_template"  // optional custom template
})
```

### List User Threads

```javascript
// Get all threads for a user
tools.list_threads({
  user_id: "user_123"
})
```

### Query Graph Nodes

```javascript
// Get entity nodes from the graph
tools.get_user_nodes({
  user_id: "user_123",
  limit: 10
})
```

### Query Graph Edges

```javascript
// Get relationship edges
tools.get_user_edges({
  user_id: "user_123",
  limit: 10
})
```

### Get Episodes

```javascript
// Get recent data ingestion events
tools.get_episodes({
  user_id: "user_123",
  lastn: 5
})
```

## Development

### Running Tests

```bash
# Run unit tests
go test ./...

# Run integration tests (requires ZEP_API_KEY)
export ZEP_API_KEY=your-key
go test ./test/integration/... -v

# Run all tests
go test -v ./...
```

### Building

```bash
# Build for current platform
go build -o zep-mcp-server cmd/server/main.go

# Build for multiple platforms
GOOS=darwin GOARCH=arm64 go build -o zep-mcp-server-darwin-arm64 cmd/server/main.go
GOOS=darwin GOARCH=amd64 go build -o zep-mcp-server-darwin-amd64 cmd/server/main.go
GOOS=linux GOARCH=amd64 go build -o zep-mcp-server-linux-amd64 cmd/server/main.go
GOOS=windows GOARCH=amd64 go build -o zep-mcp-server-windows-amd64.exe cmd/server/main.go
```

### Project Structure

```
mcp/zep-mcp-server/
├── cmd/server/           # Main entry point
├── internal/
│   ├── config/          # Configuration management
│   ├── handlers/        # Tool handlers
│   ├── server/          # MCP server setup
│   └── transform/       # Validation and formatting utilities
├── pkg/zep/             # Zep client wrapper
├── test/
│   ├── client/          # Test harness using Go MCP SDK
│   └── integration/     # Integration tests
└── docs/                # Additional documentation
```

## Troubleshooting

### Server won't start

**Problem:** `ZEP_API_KEY environment variable is required`

**Solution:** Set your API key in `.env` file or as an environment variable:
```bash
export ZEP_API_KEY=your-key
```

### Connection errors

**Problem:** Tool calls fail with connection errors

**Solution:** Verify your API key is valid and check your network connectivity. Test with:
```bash
curl -H "Authorization: Bearer $ZEP_API_KEY" https://api.getzep.com/api/v2/users
```

### User/Thread not found errors

**Problem:** `404 Not Found` errors for users or threads

**Solution:** These are expected if the user or thread doesn't exist in your Zep Cloud instance. Create them first via the Zep SDK or API.

### Slow performance

**Problem:** Tool calls take a long time

**Solution:**
- Reduce `limit` parameters in search queries
- Use search filters (`node_labels`, `edge_types`) to narrow results
- Check your network latency to Zep Cloud

## Documentation

- [Tool Reference](docs/TOOLS.md) - Detailed documentation for each tool
- [Docker Deployment Guide](docs/DOCKER.md) - Docker and Docker Compose usage
- [Zep Cloud Documentation](https://help.getzep.com/) - Zep Cloud guides and API reference
- [MCP Specification](https://modelcontextprotocol.io/) - Model Context Protocol docs

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/getzep/zep/issues)
- **Documentation**: [Zep Cloud Docs](https://help.getzep.com/)
- **Community**: [Zep Discord](https://discord.gg/W8Kw6bsgXQ)

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## Credits

Built with:
- [Zep Go SDK v3](https://github.com/getzep/zep-go)
- [MCP Go SDK](https://github.com/modelcontextprotocol/go-sdk)
- [godotenv](https://github.com/joho/godotenv)
