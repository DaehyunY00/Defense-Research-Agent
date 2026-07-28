"""Shared Pydantic types and validation rules for domain models."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

ID_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,127})$"
CHECKSUM_PATTERN = r"^[0-9a-f]{64}$"
LANGUAGE_PATTERN = r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"

type EntityId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=ID_PATTERN,
    ),
]
type Label = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
type Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
type Score = Annotated[float, Field(ge=0.0, le=100.0)]
type Checksum = Annotated[str, StringConstraints(pattern=CHECKSUM_PATTERN)]
type LanguageCode = Annotated[str, StringConstraints(pattern=LANGUAGE_PATTERN)]
type JsonObject = dict[str, JsonValue]


class DomainModel(BaseModel):
    """Base class with strict field names and assignment validation."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
    )
