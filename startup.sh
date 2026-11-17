#!/bin/bash

# ----------------------------------------------------------------------
# FOR PRODUCTION DEPLOYMENT ONLY
# ----------------------------------------------------------------------
# For development, use 'uv run working_files/dev.py' to start the app with auto-reload and docs rebuilding.

# This script downloads the vectordb using azcopy and then starts the application



set -e  # Exit on any error

VERSION=$(grep -m1 '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
echo "TAIC Smart Assistant version ${VERSION}"

# Check if vectordb directory already exists and has content
if [ -d "/app/vectordb/all_document_types.lance" ] && [ "$(ls -A /app/vectordb/all_document_types.lance 2>/dev/null)" ]; then
    echo "Vector database already exists, skipping download..."
else
    echo "Vector database not found, downloading..."

    # Microsoft Entra / managed identity only: no SAS token or account key.
    # AZURE_CLIENT_ID selects a user-assigned identity (for example the
    # dev/beta test identity). Without it, azcopy uses the system-assigned one.
    echo "Downloading vector database using managed identity..."
    if [ -n "${AZURE_CLIENT_ID:-}" ]; then
        azcopy login --identity --identity-client-id "${AZURE_CLIENT_ID}"
    else
        azcopy login --identity
    fi
    ACCOUNT_NAME="${AZURE_STORAGE_ACCOUNT_NAME:-taicdocumentsearcherdata}"
    # VECTORDB_DOWNLOAD_PATH may include the container name, e.g.
    # "vectordb/archive/2026-07-25_17-50-08_prod". Default to the live prod prefix.
    VECTORDB_SOURCE="${VECTORDB_DOWNLOAD_PATH:-vectordb/prod}"
    VECTORDB_SOURCE="${VECTORDB_SOURCE#/}"
    VECTORDB_SOURCE="${VECTORDB_SOURCE#vectordb/}"
    azcopy cp "https://${ACCOUNT_NAME}.blob.core.windows.net/vectordb/${VECTORDB_SOURCE}/*" /app/vectordb --recursive

    echo "Vector database download completed!"
fi

echo "Starting the application on port '${PORT}'..."


# Start the FastAPI application
# Use PORT environment variable, default to 8080 if not set
exec uvicorn app:app --host 0.0.0.0 --port ${PORT}