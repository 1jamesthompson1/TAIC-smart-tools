"""Shared Microsoft Entra credentials for Azure OpenAI."""

import openai
from azure.identity import DefaultAzureCredential, get_bearer_token_provider


def get_openai_client(endpoint: str | None = None) -> openai.OpenAI:
    """Return an Azure OpenAI client authenticated with Microsoft Entra ID.

    Args:
        endpoint: Azure OpenAI endpoint, including /openai/v1/ when applicable.

    Returns:
        openai.OpenAI: Client for chat, responses, and embeddings.
    """
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(exclude_interactive_browser_credential=True),
        "https://cognitiveservices.azure.com/.default",
    )
    client_kwargs: dict[str, object] = {"api_key": token_provider}
    if endpoint:
        client_kwargs["base_url"] = endpoint
    return openai.OpenAI(**client_kwargs)
