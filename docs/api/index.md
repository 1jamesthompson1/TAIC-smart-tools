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

### [Utilities](utilities.md)

## Using This Reference

Each module page includes:

- **Classes**: All classes with their methods and attributes
- **Functions**: Standalone functions
- **Type Hints**: Parameter and return types
- **Docstrings**: Detailed descriptions and examples

Navigate using the sidebar or click the module links above.

## An example docstring

```python
def your_function(param1: str, param2: int) -> Result:
    """Short description.

    Longer description if needed.

    Args:
        param1: Description of param1.
        param2: Description of param2.

    Returns:
        Description of return value.

    Examples:
        Basic usage:

        >>> result = your_function("test", 42)
        >>> print(result)
        Result(value='test', count=42)

        Or with a more complete example:

        from mymodule import your_function
        
        # Setup
        data = prepare_data()
        
        # Execute
        result = your_function(data.name, data.count)
        
        # Result
        print(result.value)  # Output: 'test'
    """
```

## Contributing

When adding new code:

- Use type hints for all parameters and returns
- Write clear, descriptive docstrings
- Include examples in docstrings where helpful
- Follow Google-style docstring format

See the [Contributing Guide](../developer-guide/contributing.md) for more details.
