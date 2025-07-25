# Zep Integrations

This directory contains dedicated integration packages for Zep with various AI frameworks and libraries. Each integration is packaged separately to allow users to install only what they need.

## Available Integrations

### Python Integrations

#### AutoGen Integration (`zep-autogen`)
- **Package**: `zep-autogen`
- **Location**: [`python/autogen/`](python/autogen/)
- **Description**: Memory integration for Microsoft AutoGen agents
- **Install**: `pip install zep-autogen`

#### More Integrations Coming Soon
Additional integrations for CrewAI, LangChain, LlamaIndex, and other frameworks are planned.

### TypeScript Integrations - Coming Soon

Future TypeScript/JavaScript integrations will be available in the [`typescript/`](typescript/) directory.

## Package Structure

Each integration follows a consistent structure:

```
integrations/{language}/{framework}/
├── src/
│   └── zep_{framework}/          # Main package code
│       ├── __init__.py
│       ├── memory.py             # Core memory integration
│       └── exceptions.py         # Framework-specific exceptions
├── tests/                        # Test files
├── examples/                     # Usage examples
├── pyproject.toml               # Package configuration
├── README.md                    # Package documentation
└── CHANGELOG.md                 # Version history
```

## Development

### Building and Testing

Each package can be built and tested independently:

```bash
# Navigate to specific integration
cd integrations/python/autogen

# Install in development mode
uv sync --extra dev

# Run tests
uv run pytest

# Build package
uv build
```

### Adding New Integrations

1. **Create Package Structure**: Follow the template structure above
2. **Implement Integration**: Create the memory interface for your framework
3. **Add Tests**: Comprehensive test coverage for the integration
4. **Update CI/CD**: The GitHub Actions will automatically detect and build new packages
5. **Documentation**: Add README and examples

## Release Process

Each integration package is released independently:

- **Automatic**: Version bumps in `pyproject.toml` trigger releases
- **Manual**: Use GitHub Actions workflow dispatch
- **CI/CD**: Automated testing across Python 3.10-3.13

## Support

- [Zep Documentation](https://docs.getzep.com)
- [GitHub Issues](https://github.com/getzep/zep/issues)
- [Community Discord](https://discord.gg/zep)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](../CONTRIBUTING.md) for details on:

- Code style and standards
- Testing requirements
- Pull request process
- Release procedures