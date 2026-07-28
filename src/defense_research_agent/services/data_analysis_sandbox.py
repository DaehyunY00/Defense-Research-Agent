"""Deterministic allow-listed analysis over configured public datasets."""

import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import TypeGuard, cast

from pydantic import JsonValue, TypeAdapter

from defense_research_agent.domain import (
    DataAnalysisDataset,
    DataAnalysisDatasetDescriptor,
    DataAnalysisFilter,
    DataAnalysisOperation,
    DataAnalysisRequest,
    DataAnalysisResult,
    DataFilterOperator,
    DataScalar,
    JsonObject,
    ResearchBrief,
    ResearchTask,
    ResearchToolEvidence,
    ResearchToolFailure,
    ResearchToolOutput,
    ToolCapability,
)
from defense_research_agent.services.research_tools import ResearchToolAdapter

_MAX_REGISTRY_BYTES = 5 * 1024 * 1024
_DATASET_LIST_ADAPTER = TypeAdapter(list[DataAnalysisDataset])


class DataAnalysisExecutionError(ValueError):
    """Expected rejected request with a stable public failure code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DataAnalysisDatasetRegistry:
    """Immutable lookup of small reviewed public datasets."""

    def __init__(
        self,
        datasets: Sequence[DataAnalysisDataset],
        *,
        max_serialized_bytes: int = _MAX_REGISTRY_BYTES,
    ) -> None:
        if max_serialized_bytes <= 0:
            raise ValueError("max_serialized_bytes must be positive")
        by_id: dict[str, DataAnalysisDataset] = {}
        serialized_bytes = 0
        for dataset in datasets:
            if dataset.dataset_id in by_id:
                raise ValueError(f"duplicate data analysis dataset: {dataset.dataset_id}")
            serialized_bytes += len(_canonical_json(dataset.model_dump(mode="json")))
            by_id[dataset.dataset_id] = dataset
        if not by_id:
            raise ValueError("at least one data analysis dataset is required")
        if serialized_bytes > max_serialized_bytes:
            raise ValueError("data analysis dataset registry exceeds its byte limit")
        self._datasets = by_id

    @classmethod
    def from_json_file(cls, path: Path) -> "DataAnalysisDatasetRegistry":
        """Load a deployer-selected registry file, never a model-selected path."""
        return cls.from_json_text(path.read_text(encoding="utf-8"))

    @classmethod
    def from_json_text(cls, content: str) -> "DataAnalysisDatasetRegistry":
        """Validate a JSON array into the strict public-dataset contract."""
        return cls(_DATASET_LIST_ADAPTER.validate_json(content))

    def get(self, dataset_id: str) -> DataAnalysisDataset:
        """Resolve one exact configured ID or reject the request."""
        dataset = self._datasets.get(dataset_id)
        if dataset is None:
            raise DataAnalysisExecutionError(
                "dataset_not_found",
                f"dataset is not configured: {dataset_id}",
            )
        return dataset

    def catalog(self) -> tuple[DataAnalysisDatasetDescriptor, ...]:
        """Return row-free metadata that a planner may safely inspect."""
        return tuple(
            DataAnalysisDatasetDescriptor(
                dataset_id=dataset.dataset_id,
                title=dataset.title,
                source_locator=dataset.source_locator,
                source_evidence_ids=dataset.source_evidence_ids,
                columns=sorted({column for row in dataset.rows for column in row}),
                row_count=len(dataset.rows),
            )
            for dataset in self._datasets.values()
        )


class DataAnalysisSandboxAdapter(ResearchToolAdapter):
    """Execute only fixed statistical operations; code and SQL are impossible inputs."""

    capability = ToolCapability.DATA_ANALYSIS_SANDBOX

    def __init__(self, registry: DataAnalysisDatasetRegistry) -> None:
        self._registry = registry

    def execute(self, brief: ResearchBrief, task: ResearchTask) -> ResearchToolOutput:
        """Preserve successful results when a sibling analysis request fails."""
        del brief
        evidence: list[ResearchToolEvidence] = []
        failures: list[ResearchToolFailure] = []
        for request in task.data_analysis_requests:
            try:
                dataset = self._registry.get(request.dataset_id)
                result = _execute_request(request, dataset)
                evidence.append(_to_evidence(result, dataset))
            except DataAnalysisExecutionError as error:
                failures.append(
                    ResearchToolFailure(
                        capability=self.capability,
                        code=error.code,
                        message=str(error),
                    )
                )
        return ResearchToolOutput(
            capability=self.capability,
            evidence=evidence,
            failures=failures,
        )


def load_default_data_analysis_registry() -> DataAnalysisDatasetRegistry:
    """Load the small public-only demonstration registry bundled in the wheel."""
    resource = files("defense_research_agent.resources").joinpath(
        "default_data_analysis_datasets.json"
    )
    return DataAnalysisDatasetRegistry.from_json_text(resource.read_text(encoding="utf-8"))


def _execute_request(
    request: DataAnalysisRequest,
    dataset: DataAnalysisDataset,
) -> DataAnalysisResult:
    columns = {column for row in dataset.rows for column in row}
    referenced_columns = {
        value
        for value in (
            *(data_filter.column for data_filter in request.filters),
            request.group_by,
            request.value_column,
            request.second_value_column,
        )
        if value is not None
    }
    unknown_columns = referenced_columns - columns
    if unknown_columns:
        raise DataAnalysisExecutionError(
            "column_not_found",
            f"dataset {dataset.dataset_id} has no columns: {sorted(unknown_columns)}",
        )

    filtered_rows = [
        row
        for row in dataset.rows
        if all(_matches_filter(row, data_filter) for data_filter in request.filters)
    ]
    output, caveats = _calculate(request, filtered_rows)
    return DataAnalysisResult(
        result_id=f"analysis:{request.request_id}",
        request_id=request.request_id,
        dataset_id=dataset.dataset_id,
        operation=request.operation,
        input_sha256=_dataset_checksum(dataset),
        source_row_count=len(dataset.rows),
        filtered_row_count=len(filtered_rows),
        output=output,
        caveats=caveats,
    )


def _calculate(
    request: DataAnalysisRequest,
    rows: Sequence[dict[str, DataScalar]],
) -> tuple[JsonObject, list[str]]:
    if request.operation is DataAnalysisOperation.ROW_COUNT:
        return {"row_count": len(rows)}, []
    if request.operation is DataAnalysisOperation.DESCRIBE_NUMERIC:
        column = _required(request.value_column)
        values, missing_count = _numeric_values(rows, column)
        if not values:
            raise DataAnalysisExecutionError(
                "insufficient_data",
                f"column {column} has no numeric values after filtering",
            )
        output: JsonObject = {
            "column": column,
            "count": len(values),
            "missing_count": missing_count,
            "minimum": min(values),
            "maximum": max(values),
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "population_standard_deviation": statistics.pstdev(values),
        }
        return output, _missing_caveat(missing_count)
    if request.operation is DataAnalysisOperation.GROUP_COUNT:
        group_by = _required(request.group_by)
        counts: dict[DataScalar, int] = defaultdict(int)
        for row in rows:
            counts[row.get(group_by)] += 1
        group_counts = [
            {"group": group, "count": counts[group]}
            for group in sorted(counts, key=_scalar_sort_key)
        ]
        return {"group_by": group_by, "groups": cast(JsonValue, group_counts)}, []
    if request.operation is DataAnalysisOperation.GROUP_MEAN:
        group_by = _required(request.group_by)
        value_column = _required(request.value_column)
        values_by_group: dict[DataScalar, list[float]] = defaultdict(list)
        missing_by_group: dict[DataScalar, int] = defaultdict(int)
        for row in rows:
            group = row.get(group_by)
            value = row.get(value_column)
            if value is None:
                missing_by_group[group] += 1
            elif _is_number(value):
                values_by_group[group].append(float(value))
            else:
                raise DataAnalysisExecutionError(
                    "non_numeric_column",
                    f"column {value_column} contains a non-numeric value",
                )
        all_groups = set(values_by_group) | set(missing_by_group)
        group_means: list[JsonObject] = []
        for group in sorted(all_groups, key=_scalar_sort_key):
            values = values_by_group[group]
            group_means.append(
                {
                    "group": group,
                    "count": len(values),
                    "missing_count": missing_by_group[group],
                    "mean": statistics.fmean(values) if values else None,
                }
            )
        total_missing = sum(missing_by_group.values())
        return {
            "group_by": group_by,
            "value_column": value_column,
            "groups": cast(JsonValue, group_means),
        }, _missing_caveat(total_missing)

    first_column = _required(request.value_column)
    second_column = _required(request.second_value_column)
    pairs: list[tuple[float, float]] = []
    missing_count = 0
    for row in rows:
        first = row.get(first_column)
        second = row.get(second_column)
        if first is None or second is None:
            missing_count += 1
            continue
        if not _is_number(first) or not _is_number(second):
            raise DataAnalysisExecutionError(
                "non_numeric_column",
                f"correlation columns {first_column} and {second_column} must be numeric",
            )
        pairs.append((float(first), float(second)))
    if len(pairs) < 2:
        raise DataAnalysisExecutionError(
            "insufficient_data",
            "correlation requires at least two complete numeric row pairs",
        )
    first_values = [pair[0] for pair in pairs]
    second_values = [pair[1] for pair in pairs]
    first_mean = statistics.fmean(first_values)
    second_mean = statistics.fmean(second_values)
    numerator = math.fsum((first - first_mean) * (second - second_mean) for first, second in pairs)
    first_scale = math.fsum((value - first_mean) ** 2 for value in first_values)
    second_scale = math.fsum((value - second_mean) ** 2 for value in second_values)
    denominator = math.sqrt(first_scale * second_scale)
    if denominator == 0:
        raise DataAnalysisExecutionError(
            "zero_variance",
            "correlation is undefined when either column has zero variance",
        )
    return {
        "value_column": first_column,
        "second_value_column": second_column,
        "paired_count": len(pairs),
        "missing_pair_count": missing_count,
        "pearson_correlation": numerator / denominator,
    }, _missing_caveat(missing_count)


def _matches_filter(
    row: dict[str, DataScalar],
    data_filter: DataAnalysisFilter,
) -> bool:
    actual = row.get(data_filter.column)
    expected = data_filter.value
    if data_filter.operator is DataFilterOperator.EQUALS:
        return actual == expected
    if data_filter.operator is DataFilterOperator.NOT_EQUALS:
        return actual != expected
    if not _is_number(actual) or not _is_number(expected):
        raise DataAnalysisExecutionError(
            "invalid_filter",
            f"ordering filter for {data_filter.column} requires numeric values",
        )
    left = float(actual)
    right = float(expected)
    if data_filter.operator is DataFilterOperator.LESS_THAN:
        return left < right
    if data_filter.operator is DataFilterOperator.LESS_THAN_OR_EQUAL:
        return left <= right
    if data_filter.operator is DataFilterOperator.GREATER_THAN:
        return left > right
    return left >= right


def _numeric_values(
    rows: Sequence[dict[str, DataScalar]],
    column: str,
) -> tuple[list[float], int]:
    values: list[float] = []
    missing_count = 0
    for row in rows:
        value = row.get(column)
        if value is None:
            missing_count += 1
        elif _is_number(value):
            values.append(float(value))
        else:
            raise DataAnalysisExecutionError(
                "non_numeric_column",
                f"column {column} contains a non-numeric value",
            )
    return values, missing_count


def _to_evidence(
    result: DataAnalysisResult,
    dataset: DataAnalysisDataset,
) -> ResearchToolEvidence:
    output_json = json.dumps(
        result.output,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ResearchToolEvidence(
        evidence_id=result.result_id,
        capability=ToolCapability.DATA_ANALYSIS_SANDBOX,
        title=f"{dataset.title}: {result.operation.value}",
        excerpt=output_json[:4_000],
        source_type="sandbox:data_analysis",
        locator=dataset.source_locator,
        metadata={
            "request_id": result.request_id,
            "dataset_id": result.dataset_id,
            "operation": result.operation.value,
            "input_sha256": result.input_sha256,
            "source_row_count": result.source_row_count,
            "filtered_row_count": result.filtered_row_count,
            "output": result.output,
            "caveats": cast(JsonValue, result.caveats),
            "source_evidence_ids": cast(JsonValue, dataset.source_evidence_ids),
            "execution_mode": result.execution_mode,
            "arbitrary_code_executed": False,
            "arbitrary_sql_executed": False,
            "source_mutated": False,
            "deployed": False,
        },
    )


def _dataset_checksum(dataset: DataAnalysisDataset) -> str:
    return hashlib.sha256(
        _canonical_json(dataset.model_dump(mode="json")),
    ).hexdigest()


def _canonical_json(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _required(value: str | None) -> str:
    if value is None:
        raise RuntimeError("validated analysis request is missing a required column")
    return value


def _is_number(value: DataScalar) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _scalar_sort_key(value: DataScalar) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _missing_caveat(missing_count: int) -> list[str]:
    if missing_count == 0:
        return []
    return [f"{missing_count}개 결측값을 계산에서 제외했다."]
