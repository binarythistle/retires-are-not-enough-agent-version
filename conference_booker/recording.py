"""Saves every raw model request and response to disk, one JSON file per exchange, so a real
run can be turned into WireMock stubs (see wiremock/generate_llm_stubs.py).

Hooks the HTTP client under the OpenAI SDK, so the files hold exactly the bytes on the wire.
Headers are deliberately not written: the request carries the API key.
"""

from __future__ import annotations

import itertools
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from openai import DefaultAsyncHttpxClient

logger = logging.getLogger(__name__)


def recording_http_client(directory: str) -> httpx.AsyncClient:
    folder = Path(directory).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    # One prefix per process run, so a second run never overwrites the first.
    run_prefix = datetime.now().strftime("%Y%m%d-%H%M%S")
    sequence = itertools.count(1)

    async def record(response: httpx.Response) -> None:
        await response.aread()
        exchange = {
            "method": response.request.method,
            "uri": str(response.request.url),
            "request": _parse_or_text(response.request.content),
            "status": response.status_code,
            "response": _parse_or_text(response.content),
        }
        path = folder / f"{run_prefix}-{next(sequence):03d}.json"
        path.write_text(json.dumps(exchange, indent=2, ensure_ascii=False))

    logger.info("Recording model exchanges to %s/%s-NNN.json", folder, run_prefix)
    return DefaultAsyncHttpxClient(event_hooks={"response": [record]})


def _parse_or_text(body: bytes) -> Any:
    """Bodies are stored as JSON when they are JSON, so the files read like stubs."""
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return body.decode("utf-8", errors="replace")
