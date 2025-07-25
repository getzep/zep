# Claude's Guide to Building Zep Framework Integrations

This document captures the learnings, patterns, and requirements for building Zep integrations with AI frameworks based on the `zep-autogen` integration development.

## Project Structure

Each integration should follow this standardized structure:

```
integrations/python/{framework}/
├── src/zep_{framework}/
│   ├── __init__.py           # Package entry point with version
│   ├── memory.py             # Core memory integration class
│   └── exceptions.py         # Framework-specific exceptions
├── tests/
│   └── test_basic.py         # Comprehensive test suite
├── examples/
│   └── {framework}_basic.py  # Working example
├── pyproject.toml            # Package configuration
├── README.md                 # Package documentation
├── CHANGELOG.md              # Version history
└── Makefile                  # Development commands
```

## Core Implementation Requirements

### 1. Memory Class Interface

Each framework integration must implement the framework's memory interface:

**For AutoGen example:**
```python
from autogen_core.memory import Memory, MemoryContent, MemoryQueryResult
from autogen_core import CancellationToken

class ZepMemory(Memory):
    async def add(self, content: MemoryContent, cancellation_token: CancellationToken | None = None) -> None:
        # Implementation
    
    async def query(self, query: str | MemoryContent, cancellation_token: CancellationToken | None = None, **kwargs) -> MemoryQueryResult:
        # Implementation
```

**Key principles:**
- Always match the framework's exact interface signatures
- Handle both required and optional parameters properly
- Use proper type hints for all parameters and return types
- Include comprehensive error handling

### 2. Zep Integration Patterns

**Storage Strategy:**
- **Messages**: Store in Zep threads using `thread.add_messages()`
- **Data/Facts**: Store in Zep user graphs using `graph.add()`
- **Context**: Retrieve using `thread.get_user_context()` and `graph.search()`

**Metadata Handling:**
```python
# Use metadata.type to determine storage method
metadata_copy = content.metadata.copy() if content.metadata else {}
content_type = metadata_copy.get("type", "data")

if content_type == "message":
    # Store as thread message
elif content_type == "data":
    # Store as graph data
```

**MIME Type Mapping:**
```python
mime_to_data_type = {
    MemoryMimeType.TEXT: "text",
    MemoryMimeType.MARKDOWN: "text", 
    MemoryMimeType.JSON: "json",
}
```

### 3. Package Configuration Template

**pyproject.toml essentials:**
```toml
[project]
name = "zep-{framework}"
version = "0.1.0"
description = "{Framework} integration for Zep"
requires-python = ">=3.10"  # Match framework requirements
dependencies = [
    "zep-cloud>=3.0.0rc1",
    "{framework-specific-deps}",
    "typing-extensions>=4.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-cov",
    "pytest-asyncio", 
    "ruff>=0.1.0",
    "mypy>=1.0.0",
]

[tool.mypy]
python_version = "3.10"  # Conservative target
# ... mypy config

[tool.ruff]
target-version = "py310"  # Match mypy
# ... ruff config
```

## Testing Strategy

### Test Categories

1. **Basic Import/Structure Tests**
   - Package imports correctly
   - Version accessibility
   - Package structure validation

2. **Mock Client Tests** 
   - Memory initialization with mocks
   - Parameter validation
   - Method call verification
   - Error handling

3. **Integration Tests** (optional, requires API key)
   - Real Zep client functionality
   - End-to-end workflows

### Mock Testing Pattern
```python
@pytest.mark.asyncio
async def test_memory_method_with_mock(self):
    mock_client = MagicMock(spec=AsyncZep)
    mock_client.thread = MagicMock()
    mock_client.thread.add_messages = AsyncMock()
    
    memory = ZepMemory(client=mock_client, user_id="test", thread_id="test")
    # Test implementation
    
    mock_client.thread.add_messages.assert_called_once()
```

## Development Workflow

### Makefile Commands
Each integration should include a Makefile with:
- `make format` - Code formatting
- `make lint` - Linting checks  
- `make type-check` - MyPy validation
- `make test` - Run test suite
- `make all` - Full development workflow
- `make pre-commit` - Pre-commit checks with auto-fixes
- `make ci` - Strict CI-style checks

### Development Process
1. **Setup**: `make install` - Install dependencies
2. **Code**: Implement memory interface
3. **Test**: `make test` - Verify functionality  
4. **Check**: `make pre-commit` - Ensure code quality
5. **Verify**: `make ci` - Final validation

## Common Pitfalls & Solutions

### 1. Interface Compliance
**Problem**: MyPy errors about interface mismatches
**Solution**: Always check the framework's exact interface using:
```python
import inspect
print(inspect.signature(FrameworkMemory.method))
```

### 2. Type Safety
**Problem**: Runtime type errors with framework objects
**Solution**: Use proper type guards and safe conversions:
```python
if isinstance(content.mime_type, MemoryMimeType):
    data_type = mime_to_data_type.get(content.mime_type, "text")
else:
    data_type = "text"  # Safe fallback
```

### 3. Async Client Management
**Problem**: Zep client lifecycle management
**Solution**: Don't close externally provided clients:
```python
async def close(self) -> None:
    # The client was provided externally, caller manages it
    pass
```

### 4. Null Safety
**Problem**: Optional thread_id causing runtime errors
**Solution**: Guard against None values:
```python
if not self._thread_id:
    return DefaultResult()
await self._client.thread.method(thread_id=self._thread_id)
```

## GitHub Actions Integration

The workflows automatically handle:
- Multi-version Python testing (3.10-3.13)
- Linting, type checking, and tests
- Package building and PyPI publishing
- Path-based change detection

**Required workflow updates:**
- Add new package to `test-integrations.yml` filters
- Package name follows `zep_{framework}` pattern
- Release tags follow `zep-{framework}-v{version}` format

## Dependencies Management

### Version Strategy
- **Python**: Support >=3.10 (broad compatibility)
- **Zep**: Pin to specific version (`>=3.0.0rc1`)
- **Framework**: Use framework's minimum requirements
- **Dev tools**: Use recent stable versions

### Dependency Conflicts
When frameworks have conflicting dependencies:
1. Use optional dependencies `[project.optional-dependencies]`
2. Document compatibility in README
3. Consider separate environments for testing

## Documentation Requirements

### README.md Must Include:
- Quick installation instructions
- Basic usage example  
- Configuration options
- Link to full documentation
- Troubleshooting section

### Code Documentation:
- Comprehensive docstrings for all public methods
- Type hints for all parameters and returns
- Usage examples in docstrings
- Error condition documentation

## Release Process

1. **Version Bump**: Update `version` in `pyproject.toml`
2. **Changelog**: Document changes in `CHANGELOG.md`
3. **Testing**: Run `make ci` to verify all checks pass
4. **Release**: Create GitHub release with tag `zep-{framework}-v{version}`
5. **Automation**: GitHub Actions handles PyPI publishing

## Framework-Specific Considerations

### AutoGen Specifics
- Implements `autogen_core.memory.Memory` interface
- Uses `MemoryContent` and `MemoryQueryResult` types
- Supports `CancellationToken` for async operations
- Metadata-driven storage routing (message vs data)

### Future Framework Notes
Document framework-specific patterns here as new integrations are built:

- **CrewAI**: [TBD - memory interface patterns]
- **LangChain**: [TBD - chat memory integration]  
- **LlamaIndex**: [TBD - index integration patterns]

## Quality Standards

Every integration must:
- ✅ Pass all linting checks (ruff)
- ✅ Pass type checking (mypy) 
- ✅ Have >90% test coverage
- ✅ Include working examples
- ✅ Follow consistent naming conventions
- ✅ Handle errors gracefully
- ✅ Support async operations properly
- ✅ Document all public APIs

---

## Quick Reference Commands

```bash
# Setup new integration
mkdir integrations/python/{framework}
cp -r integrations/python/zep_autogen/* integrations/python/{framework}/

# Development workflow  
make install          # Setup dependencies
make pre-commit      # Development checks
make ci              # CI validation

# Release
git tag zep-{framework}-v{version}
git push origin zep-{framework}-v{version}
```

This guide should be updated as new patterns emerge from building additional integrations.