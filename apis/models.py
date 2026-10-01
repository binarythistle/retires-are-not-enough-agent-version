"""Shared helpers for both APIs: camelCase JSON to match the .NET wire format."""

from __future__ import annotations

import uuid

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


def booking_reference(prefix: str, length: int) -> str:
    """Mirrors the .NET `$"{prefix}{Guid:N}"[..length]`, uppercased."""
    return f"{prefix}{uuid.uuid4().hex}"[:length].upper()


def error(status_code: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)
