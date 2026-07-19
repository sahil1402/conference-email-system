"""Shared OpenAI-compatible chat-completions POST with param fallback.

Hosted / reasoning models differ in which request params they accept: some
require ``max_completion_tokens`` instead of ``max_tokens``; reasoning models
reject a non-default ``temperature`` (only the default is allowed). This POSTs
once and, on a 400 that names such a param, fixes exactly that param and retries
(bounded), so the drafter and distiller stay model-agnostic and still ask for
the most deterministic settings each model supports.
"""

import httpx


async def post_chat(
    client: httpx.AsyncClient, url: str, payload: dict, headers: dict | None
) -> httpx.Response:
    """POST a chat-completions request, adapting to model-specific 400s.

    Mutates ``payload`` in place as it drops/swaps unsupported params. Returns the
    final response (which the caller still checks with ``raise_for_status()``).
    """
    response = await client.post(url, json=payload, headers=headers)
    # At most two fixes: max_tokens→max_completion_tokens, and dropping temperature.
    for _ in range(2):
        if response.status_code != 400:
            break
        body = response.text
        if "max_completion_tokens" in body and "max_tokens" in payload:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        elif "temperature" in body and "temperature" in payload:
            payload.pop("temperature")
        else:
            break
        response = await client.post(url, json=payload, headers=headers)
    return response
