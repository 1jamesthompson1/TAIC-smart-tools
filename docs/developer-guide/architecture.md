# Architecture

High-level overview of TAIC Smart Tools architecture.

## Components

### Frontend
- **Gradio UI**: User interface with chat and search interfaces
- **FastAPI**: Backend API and routing layer

### Backend Modules

#### Assistant (`backend/Assistant.py`)
Manages AI conversations using Azure OpenAI. Key classes:

- `CompleteHistory`: Conversation history management
- `AssistantPrompts`: System prompt generation
- `Assistant`: Main orchestrator for AI interactions

#### AssistantTools (`backend/AssistantTools.py`)
Tools the assistant can call:

- `SearchTool`: Knowledge base search
- `ReadReportTool`: Full report retrieval
- `ReasoningTool`: Extended thinking
- `DocumentationTool`: Internal docs access

#### Searching (`backend/Searching.py`)
Vector-based semantic search with LanceDB. Key classes:

- `Searcher`: Main search interface
- `AzureAITextEmbeddingFunction`: Text to embeddings
- `SearchParams`: Search configuration
- `GraphMaker`: Analytics visualizations

#### Storage (`backend/Storage.py`)
Data persistence using Azure Storage:

- **Blob Storage**: Conversation/search content
- **Table Storage**: Metadata and indexes

#### Version (`backend/Version.py`)
Version management and compatibility checking.

## Technology Stack

- **Python 3.10-3.12**: Runtime
- **Gradio + FastAPI**: UI and API framework
- **Azure OpenAI**: Language model (Can be any AI model on Azure AI foundry)
- **LanceDB**: Vector database
- **Azure Storage**: Data persistence
- **OAuth 2.0**: Authentication via Azure AD

## Deployment

- Dockerized application
- Environment-based configuration  
- Azure-hosted services
