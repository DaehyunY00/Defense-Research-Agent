"""Structured contracts for bounded public-data analysis."""

import math
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from defense_research_agent.domain.common import (
    Checksum,
    DomainModel,
    EntityId,
    JsonObject,
    Label,
)

type DataScalar = str | int | float | bool | None
type ColumnName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,99}$",
    ),
]


class DataAnalysisOperation(StrEnum):
    """Allow-listed deterministic analysis programs."""

    ROW_COUNT = "row_count"
    DESCRIBE_NUMERIC = "describe_numeric"
    GROUP_COUNT = "group_count"
    GROUP_MEAN = "group_mean"
    PEARSON_CORRELATION = "pearson_correlation"


class DataFilterOperator(StrEnum):
    """Small comparison language; expressions and SQL are unsupported."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"


class DataAnalysisFilter(DomainModel):
    """One column-to-scalar comparison."""

    column: ColumnName
    operator: DataFilterOperator
    value: DataScalar

    @field_validator("value")
    @classmethod
    def validate_finite_filter_value(cls, value: DataScalar) -> DataScalar:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("filter values must be finite")
        return value


class DataAnalysisRequest(DomainModel):
    """One fixed analysis request against a configured dataset."""

    request_id: EntityId
    dataset_id: EntityId
    operation: DataAnalysisOperation
    filters: list[DataAnalysisFilter] = Field(default_factory=list, max_length=10)
    group_by: ColumnName | None = None
    value_column: ColumnName | None = None
    second_value_column: ColumnName | None = None

    @model_validator(mode="after")
    def validate_operation_fields(self) -> "DataAnalysisRequest":
        required: tuple[str | None, ...]
        forbidden: tuple[str | None, ...]
        if self.operation is DataAnalysisOperation.ROW_COUNT:
            required = ()
            forbidden = (self.group_by, self.value_column, self.second_value_column)
        elif self.operation is DataAnalysisOperation.DESCRIBE_NUMERIC:
            required = (self.value_column,)
            forbidden = (self.group_by, self.second_value_column)
        elif self.operation is DataAnalysisOperation.GROUP_COUNT:
            required = (self.group_by,)
            forbidden = (self.value_column, self.second_value_column)
        elif self.operation is DataAnalysisOperation.GROUP_MEAN:
            required = (self.group_by, self.value_column)
            forbidden = (self.second_value_column,)
        else:
            required = (self.value_column, self.second_value_column)
            forbidden = (self.group_by,)
            if self.value_column == self.second_value_column:
                raise ValueError("correlation requires two different numeric columns")
        if any(value is None for value in required):
            raise ValueError(f"{self.operation.value} is missing a required column")
        if any(value is not None for value in forbidden):
            raise ValueError(f"{self.operation.value} contains an unsupported column")
        return self


class DataAnalysisDataset(DomainModel):
    """Small reviewed public dataset available to the deterministic sandbox."""

    dataset_id: EntityId
    title: Label
    source_locator: Label
    source_evidence_ids: list[EntityId] = Field(default_factory=list, max_length=100)
    sensitivity: Literal["public_only"] = "public_only"
    rows: list[dict[ColumnName, DataScalar]] = Field(min_length=1, max_length=10_000)

    @field_validator("rows")
    @classmethod
    def validate_rows(
        cls,
        rows: list[dict[ColumnName, DataScalar]],
    ) -> list[dict[ColumnName, DataScalar]]:
        columns: set[str] = set()
        for row in rows:
            if not row:
                raise ValueError("data analysis rows must not be empty")
            columns.update(row)
            for value in row.values():
                if isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("data analysis values must be finite")
        if len(columns) > 50:
            raise ValueError("data analysis datasets support at most 50 columns")
        return rows


class DataAnalysisDatasetDescriptor(DomainModel):
    """Planner-visible metadata that excludes the dataset's actual rows."""

    dataset_id: EntityId
    title: Label
    source_locator: Label
    source_evidence_ids: list[EntityId] = Field(default_factory=list, max_length=100)
    columns: list[ColumnName] = Field(min_length=1, max_length=50)
    row_count: int = Field(ge=1, le=10_000)
    sensitivity: Literal["public_only"] = "public_only"


class DataAnalysisResult(DomainModel):
    """Auditable result that cannot mutate data, source, or deployment state."""

    result_id: EntityId
    request_id: EntityId
    dataset_id: EntityId
    operation: DataAnalysisOperation
    input_sha256: Checksum
    source_row_count: int = Field(ge=0)
    filtered_row_count: int = Field(ge=0)
    output: JsonObject
    caveats: list[Label] = Field(default_factory=list, max_length=20)
    execution_mode: Literal["deterministic_allow_list"] = "deterministic_allow_list"
    arbitrary_code_executed: Literal[False] = False
    arbitrary_sql_executed: Literal[False] = False
    source_mutated: Literal[False] = False
    deployed: Literal[False] = False
