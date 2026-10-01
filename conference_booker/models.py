"""Base models for the two JSON dialects the agent speaks.

- The browser and the two APIs use camelCase.
- The model sees tool results in PascalCase, because that is what the .NET agent sent and the
  WireMock LLM stubs read fields such as `$.Booking.BookingReference` from them.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import AliasChoices, AliasGenerator, BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel, to_pascal


class CamelModel(BaseModel):
    """camelCase on the wire, matching what site.js expects from the .NET app."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


def _camel_or_pascal(field_name: str) -> AliasChoices:
    return AliasChoices(to_camel(field_name), to_pascal(field_name))


class DomainModel(BaseModel):
    """Read from the APIs in camelCase, written to the model in PascalCase.

    Reading also accepts PascalCase, because Temporal's pydantic converter stores models by
    alias: a booking an activity returns has to read back from its own PascalCase payload.
    """

    model_config = ConfigDict(
        alias_generator=AliasGenerator(validation_alias=_camel_or_pascal, serialization_alias=to_pascal),
        populate_by_name=True,
        frozen=True,
    )

    def to_tool_json(self) -> str:
        return self.model_dump_json(by_alias=True)


def _json_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


# A JSON number written as .NET writes decimal (115, not "115" or 115.0).
Money = Annotated[Decimal, PlainSerializer(_json_number, return_type=int | float, when_used="json")]
