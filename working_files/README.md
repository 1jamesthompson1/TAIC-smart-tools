# Working files

This is a collection of notebook and scripts that are useful for development but want to keep from clogging up the root directory.

| name | purpose | link |
| -- | -- | -- |
| Usage logs | This can be used for either interactive investigation of the app usage logs or making a semi presentable report | [usage_logs.ipynb](usage_logs.ipynb) |
| Download vector db | This script can be run to download the latest version of the vector db from the azure storage location. Helpful for local development | [download_vector_db.py](download_vector_db.py) |
| dev.py | This is the main script for running the app and docs locally with live reload. It uses uv to run both the gradio app and the mkdocs server at the same time | [dev.py](dev.py) |
| Manage vector db | This script can be used to quickly see the status of the production db | [manage_vector_db.py](manage_vector_db.py) |
| Check embeddings | Verifies Azure OpenAI embeddings work with the current managed identity / `az login` identity, including an optional LanceDB vector search | [check_embeddings.py](check_embeddings.py) |
| Check reasoning summaries | Probes the configured model for Responses API reasoning summaries and their streaming events | [check_reasoning_summaries.py](check_reasoning_summaries.py) |
| Conversation viewer | Local FastAPI viewer for stored conversations, including tool/planning steps and estimated per-conversation API cost | [conversation_viewer.py](conversation_viewer.py) |
