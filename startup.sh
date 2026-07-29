#!/bin/bash

# ----------------------------------------------------------------------
# FOR PRODUCTION DEPLOYMENT ONLY
# ----------------------------------------------------------------------
# For development, use 'uv run working_files/dev.py' to start the app with auto-reload and docs rebuilding.

# This script downloads the vectordb using azcopy and then starts the application



set -e  # Exit on any error

echo "Starting TAIC Smart Assistant..."

# ----------------------------------------------------------------------
# Environment variables (with defaults)
# ----------------------------------------------------------------------
# VECTORDB_PATH: Local path where the vector database is stored (used by the app)
# VECTORDB_DOWNLOAD_PATH: Blob path within the storage container (starts with "vectordb/...")
# SAS_TOKEN: SAS token for Azure Blob Storage access
# STORAGE_ACCOUNT: Azure storage account name
# ----------------------------------------------------------------------
: "${VECTORDB_PATH:=vectordb}"
: "${VECTORDB_DOWNLOAD_PATH:=vectordb/prod}"
: "${STORAGE_ACCOUNT:=taicdocumentsearcherdata}"

VECTORDB_FULL_PATH="/app/${VECTORDB_PATH}"

# Check if vectordb directory already exists and has content
if [ -d "${VECTORDB_FULL_PATH}/all_document_types.lance" ] && [ "$(ls -A "${VECTORDB_FULL_PATH}/all_document_types.lance" 2>/dev/null)" ]; then
    echo "Vector database already exists at ${VECTORDB_FULL_PATH}, skipping download..."
else
    echo "Vector database not found at ${VECTORDB_FULL_PATH}, downloading..."
    
    # Check if SAS_TOKEN environment variable is set
    if [ -z "$SAS_TOKEN" ]; then
        echo "Error: SAS_TOKEN environment variable is not set."
        echo "Please set the SAS_TOKEN environment variable with a valid SAS token for the Azure Blob Storage."
        exit 1
    fi
    
    # Ensure the target directory exists
    mkdir -p "${VECTORDB_FULL_PATH}"
    
    # Download the vector database using azcopy
    echo "Downloading vector database using azcopy..."
    azcopy cp "https://${STORAGE_ACCOUNT}.blob.core.windows.net/${VECTORDB_DOWNLOAD_PATH}/*?${SAS_TOKEN}" "${VECTORDB_FULL_PATH}" --recursive
    
    echo "Vector database download completed!"
fi

echo "Starting the application on port '${PORT}'..."

# Export VECTORDB_PATH so the app picks it up (relative path resolved by the app's working directory)
export VECTORDB_PATH

# Start the FastAPI application
# Use PORT environment variable, default to 8080 if not set
exec uvicorn app:app --host 0.0.0.0 --port ${PORT}