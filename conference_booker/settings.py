"""Configuration from environment variables, shared by the plain app and (later) the worker.

OPENAI_BASE_URL is read by the OpenAI SDK itself, so pointing the agent at WireMock needs no
code here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    catalog_base_url: str
    flights_base_url: str
    # When set, every model request and response is saved here for turning into WireMock stubs.
    openai_record_to: str | None

    @classmethod
    def from_env(cls) -> Settings:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY must be set before starting Conference Booker.")

        return cls(
            openai_api_key=api_key,
            openai_model=os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
            catalog_base_url=os.environ.get("CATALOG_BASE_URL", "http://127.0.0.1:5056"),
            flights_base_url=os.environ.get("FLIGHTS_BASE_URL", "http://127.0.0.1:5057"),
            openai_record_to=os.environ.get("OPENAI_RECORD_TO") or None,
        )
