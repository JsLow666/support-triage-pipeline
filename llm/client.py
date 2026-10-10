"""
LLM client wrapper for Google Gemini.

Provides a single call_llm() function used by pipeline stages.
Reads GEMINI_API_KEY and GEMINI_MODEL from environment variables
loaded from the project .env file.
"""

import os

from google import genai
from google.genai import types


def call_llm(prompt: str) -> str:
    """
    Send a prompt to Google Gemini and return the raw text response.

    Uses JSON mode (response_mime_type='application/json') to request
    structured output from the model. Credentials and model name are
    read from environment variables set via the .env file.

    Args:
        prompt: The full prompt string to send to the model.

    Returns:
        The model's response as a raw string (expected to be valid JSON).

    Raises:
        RuntimeError: If GEMINI_API_KEY is not set in the environment.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. "
            "Copy .env.example to .env and fill in your API key."
        )

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )

    return response.text
