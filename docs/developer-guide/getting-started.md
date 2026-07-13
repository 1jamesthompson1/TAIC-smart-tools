# Getting Started

This guide will help you set up a local development environment for TAIC Smart Tools.

## Prerequisites

- Linux or WSL (Windows Subsystem for Linux)
- Python 3.10, 3.11, or 3.12
- Git
- Azure CLI
- AzCopy

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/1jamesthompson1/TAIC-smart-tools
cd TAIC-smart-tools
```

### 2. Install uv

```bash
curl -Ls https://astral.sh/uv/install.sh | sh
```

### 3. Install Dependencies

```bash
# Install project dependencies
uv sync --dev

# Setup pre-commit hooks
uv run pre-commit install
```

### 4. Download Vector Database

The vector database contains embeddings for all documents and is required for search functionality.

```bash
# Install required tools
# Follow: https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azcopy-v10?tabs=apt
# Follow: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli

# Login to Azure
az login

# Download the database
uv run python working_files/download_vector_db.py
```

### 5. Configure Environment

!!! tip
    TAIC employees: see wiki which explains how to get relevant Azure secrets and the right `.env` file.

```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your credentials
nano .env
```

Required environment variables:

- `AZURE_STORAGE_ACCOUNT_NAME`: Azure Storage account name
- `AZURE_STORAGE_ACCOUNT_KEY`: Azure Storage account key
- `AZURE_OPENAI_API_KEY`: OpenAI API key
- `AZURE_OPENAI_ENDPOINT`: OpenAI endpoint URL
- OAuth configuration for authentication

### 6. Run the Application

```bash
uv run working_files/dev.py
```

This will start both the webapp and the mkdocs documentation server with live reload. Access the webapp at `http://localhost:7860` and the docs at `http://localhost:7860/documentation`.

### 7. Access the Application

- Main interface: `http://localhost:7860`
- Tools interface: `http://localhost:7860/tools`

## Development Workflow

### Creating a Feature Branch

```bash
# Create a new branch
git checkout -b feature/your-feature-name
```

### Making Changes

1. Make your code changes
2. Run tests: `uv run pytest`
3. Format code: `uv run pre-commit run --all-files`
4. Commit with descriptive messages

### Creating a Pull Request

1. Push your branch to GitHub
2. Create a pull request with prefix:
   - `major:` for breaking changes
   - `minor:` for new features
   - `patch:` for bug fixes
3. Use squash-and-merge when merging

## Project Structure

```
TAIC_smart_assistant/
├── app.py                 # Main FastAPI/Gradio application
├── backend/               # Core backend modules
│   ├── Assistant.py       # AI assistant logic
│   ├── AssistantTools.py  # Tools for the assistant
│   ├── Searching.py       # Search functionality
│   ├── Storage.py         # Azure storage interfaces
│   └── Version.py         # Version management
├── static/                # Static web assets
├── tests/                 # Test suite
├── working_files/         # Development utilities
└── workbench/            # Experimental work
```

## Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=backend --cov-report=html

# Run specific test file
uv run pytest tests/test_assistant.py
```
## Common Tasks

### Updating Dependencies

```bash
uv sync --upgrade
```

### Adding a New Dependency

```bash
uv add package-name
# or for dev dependencies
uv add --dev package-name
```

### Debugging

The application uses Python's standard logging. Set log levels in your code:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Troubleshooting

**Module import errors?**

Ensure you're using the uv environment:

```bash
uv run python your_script.py
```

**Azure authentication issues?**

Check your `.env` file and ensure all Azure credentials are correct.

**Port already in use?**

Change the port in the uvicorn command:

```bash
uv run uvicorn app:app --host localhost --port 8000
```

## Next Steps

- Read the [Architecture Guide](architecture.md)
- Review the [API Reference](../api/index.md)
- Check the [Contributing Guidelines](contributing.md)
