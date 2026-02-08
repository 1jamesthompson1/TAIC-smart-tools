# API Reference

This section provides detailed documentation for all modules, classes, and functions in TAIC Smart Tools.

The API documentation is automatically generated from Python docstrings in the source code.

## Modules

### [Assistant](assistant.md)
Core AI assistant logic, conversation management, and prompt handling.

### [Assistant Tools](assistanttools.md)
Tools that the AI assistant can use to search, read reports, and perform reasoning.

### [Searching](searching.md)
Vector-based semantic search functionality and analytics.

### [Storage](storage.md)
Azure Storage interfaces for conversations and search sessions.

### [Version](version.md)
Version management and compatibility checking.

## Using This Reference

Each module page includes:

- **Classes**: All classes with their methods and attributes
- **Functions**: Standalone functions
- **Type Hints**: Parameter and return types
- **Docstrings**: Detailed descriptions and examples

Navigate using the sidebar or click the module links above.

## Code Examples

Throughout the API reference, you'll find examples showing how to use various components:

```python
from backend.Assistant import Assistant
from backend.Searching import Searcher

# Create a searcher
searcher = Searcher(vector_db_path="workbench/vectordb")

# Perform a search
results = searcher.search(
    query="runway safety incidents",
    max_results=10
)
```

## Contributing

When adding new code:

- Use type hints for all parameters and returns
- Write clear, descriptive docstrings
- Include examples in docstrings where helpful
- Follow Google-style docstring format

See the [Contributing Guide](../developer-guide/contributing.md) for more details.
