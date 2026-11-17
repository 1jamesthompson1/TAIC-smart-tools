# TAIC smart tools

> A platform for smart tools for internal use at the New Zealand Transport Accident Investigation Commission. Featured tools are smart assistant and smart document search.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Continuous Integration](https://github.com/1jamesthompson1/TAIC-smart-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/1jamesthompson1/TAIC-smart-tools/actions/workflows/ci.yml)
![Version](https://img.shields.io/badge/Version-0.8.1-blue)

## Preview

![TAIC Smart Assistant Demo](https://github.com/1jamesthompson1/TAIC-smart-tools/releases/download/v0.4.0/demo.gif)

---

## Features

🤖 **AI-Powered Assistant**
- Interactive chat interface with conversational AI
- Context-aware responses using TAIC's knowledge base
- Conversation history and management

🔍 **Advanced Knowledge Search**
- Vector-based semantic search across TAIC, ATSB and TSB documents (database created by [TAIC-engine](https://github.com/1jamesthompson1/TAIC-engine)
- Filter by document type, agency, year, and transport mode
- Visual analytics and result export capabilities

---

## Project Status

In November 2025 this app was deployed organisation wide at TAIC. There is no public access at the moment.

The app is currently deployed internally and privately, however it is feasible to create a new deployment that is completely separate from this one to be used by other organisations. Any interest in your own deployment or a tool like this should be directed to James Thompson (1jamesthompson1 [at] gmail dot com)

## Contributing

### Setup for Local Development

_Note that this has only ever been 'completed' on Linux machines. For ease of use I would recommend [WSL](https://learn.microsoft.com/en-us/windows/wsl/about) for windows users_

1. **Install dependencies**

```bash
# Get the code
git clone https://github.com/1jamesthompson1/TAIC-smart-tools
cd TAIC-smart-tools

# Install uv if you haven't already
curl -Ls https://astral.sh/uv/install.sh | sh

# Install project dependencies
uv sync --dev

# Setup pre-commits
uv run pre-commit install
```

2. **Get the vector database**
*This is simply so that when you want to test the webapp you can use the searcher quickly as it is locally fetching data.*

Install `azcopy` by following: https://learn.microsoft.com/en-us/azure/storage/common/storage-use-azcopy-v10?tabs=apt
Install `az cli` by following: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli?view=azure-cli-latest

You will then need to login with `az login`.

```bash
# Download and setup the vector database
uv run python working_files/download_vector_db.py
```

3. **Environment configuration**

```bash
# Copy and configure your environment variables
cp .env.example .env
# Edit .env with your Azure credentials and API keys
```

Login to Azure CLI with `az login` and ensure you have appropriate access to the resources needed.

4. **Run the app locally**

```bash
# Development mode with auto-reload (app + docs)
uv run working_files/dev.py

# Or run just the app
uv run uvicorn app:app --host localhost --port 7860 --reload --timeout-graceful-shutdown 2
```

5. **Access the application**
- Open your browser to `http://localhost:7860`
- Documentation: `http://localhost:7860/documentation`
- For the tools interface: `http://localhost:7860/tools`

### Development Workflow

The current project is setup asssuming few contributer each with full access. Therefore contribution is managed via feature branches and pull requests. Pull requests from forks cause problems with the CI/CD setup so are discouraged.

Internally at TAIC we are working with Azure DevOps. From there you can create a branch directly from a work item. This automates the first step.

1. Create a feature branch from `main`
2. Make your changes with appropriate commit messages
3. Create a pull request with a prefix of the type of change (`major:`, `minor:`, `patch:`)
4. When merging, use squash-and-merge to keep a clean history

#### Version Management

This project uses semantic versioning (MAJOR.MINOR.PATCH):

**PR auto versioning**
PR titles should include one of the following keywords to indicate the type of version bump required:

- `major:` → Major version bump
- `minor:` → Minor version bump  
- `patch:` → Patch version bump

### CI/CD Configuration

This project uses GitHub Actions for automated testing. Storage access uses
OpenID Connect (OIDC) federation to the user-assigned
`id-taicsmarttools-test` managed identity; no storage key or SAS token is used
by CI.

#### Required GitHub Secrets

The github secretes are required to give access to a managed identity that allwos for storage access and AI use as well as some other config.

| Secret | Description |
|--------|-------------|
| `AZURE_TEST_CLIENT_ID` | Client ID of the `id-taicsmarttools-test` user-assigned managed identity |
| `AZURE_TENANT_ID` | Microsoft Entra tenant ID for OIDC |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |
| `AZURE_STORAGE_ACCOUNT_NAME` | Storage account containing the test resources |
| `CONVERSATION_CONTAINER_NAME` | `test-smartassistantconversations` |
| `KNOWLEDGE_SEARCH_CONTAINER_NAME` | `test-knowledgesearchresults` |
| `CONVERSATION_METADATA_TABLE_NAME` | `testsmartassistantconversationsmetadata` |
| `KNOWLEDGE_SEARCH_LOGS_TABLE_NAME` | `testknowledgesearches` |
| `VECTORDB_PATH` | `az://vectordb/prod` (read via the Azure CLI OIDC session) |
| `VECTORDB_TABLE_NAME` | `all_document_types` |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint, including `/openai/v1/` |
| `OPENAI_MODEL` | Model/deployment used by the tests |
| `CLIENT_ID` | OAuth test client ID (placeholder is fine) |
| `CLIENT_SECRET` | OAuth test client secret (placeholder is fine) |
| `TENANT_ID` | OAuth test tenant ID (placeholder is fine) |
