# Integration Package Workflows

This directory contains GitHub Actions workflows for testing and releasing Zep integration packages.

## Workflows

### 1. `test-integrations.yml` - Pull Request Testing
Automatically tests integration packages on pull requests:
- Detects which packages have changes using path filtering
- Tests across Python 3.10-3.13
- Runs linting (ruff), type checking (mypy), and tests (pytest)
- Uploads coverage reports to Codecov

**Triggers:**
- Pull requests with changes in `integrations/python/**`
- Push to `main` branch with changes in `integrations/python/**`

### 2. `release-integrations.yml` - Package Releases
Handles publishing integration packages to PyPI:
- Triggered only by GitHub releases or manual dispatch
- Extracts package name from release tag (e.g., `zep-autogen-v0.1.0`)
- Runs full test suite before publishing
- Uses PyPI trusted publishing for security

**Triggers:**
- GitHub releases with tags like `zep-{package}-v{version}`
- Manual workflow dispatch with package selection

## Setup Requirements

### PyPI Trusted Publishing
Configure PyPI trusted publishing for each integration package:

1. **For each package** (e.g., `zep-autogen`):
   - Go to PyPI project settings
   - Add GitHub publisher with repository `getzep/zep`
   - Set workflow name: `release-integrations.yml`
   - Set environment name: `pypi`

2. **GitHub Environment**: Create a `pypi` environment in repository settings
   - Add protection rules if needed (recommended for production)
   - No secrets required (uses trusted publishing)

## Package Structure

Each integration package follows this structure:
```
integrations/python/{package}/
├── pyproject.toml          # Package configuration
├── src/zep_{package}/      # Source code
│   ├── __init__.py        # Package entry point
│   ├── memory.py          # Core integration
│   └── exceptions.py      # Error handling
├── tests/                 # Test files
├── examples/              # Usage examples
├── README.md              # Package documentation
└── CHANGELOG.md           # Version history
```

## Release Process

### Release via GitHub Releases (Recommended)
1. **Update Version**: Bump version in package's `pyproject.toml`
2. **Create Release**: Create GitHub release with tag `zep-{package}-v{version}`
   - Example: `zep-autogen-v0.1.0`
3. **Automatic Publishing**: Workflow automatically tests and publishes to PyPI

### Manual Release
1. Go to Actions tab in GitHub
2. Select "Release Integration Packages"
3. Click "Run workflow"
4. Choose specific package to release
5. Package is tested and published if successful

## Adding New Integration Packages

1. **Create Package Structure**: Follow the template structure above
2. **Update Workflows**: Add package name to the filters in both workflows
3. **Configure PyPI**: Set up trusted publishing for the new package
4. **Create Release**: Tag release as `zep-{package}-v{version}`

## Troubleshooting

- **Tests fail on PR**: Check package dependencies and Python version compatibility
- **Release fails**: Ensure PyPI trusted publishing is configured correctly
- **Package not detected**: Verify path filters include your package directory