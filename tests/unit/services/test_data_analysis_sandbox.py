"""Tests for the deterministic public-data analysis sandbox."""

from typing import cast

import pytest

from defense_research_agent.domain import (
    DataAnalysisDataset,
    DataAnalysisFilter,
    DataAnalysisOperation,
    DataAnalysisRequest,
    DataFilterOperator,
    ResearchBrief,
    ResearchRole,
    ResearchTask,
    ToolCapability,
)
from defense_research_agent.services import (
    DataAnalysisDatasetRegistry,
    DataAnalysisSandboxAdapter,
    load_default_data_analysis_registry,
)


def _dataset() -> DataAnalysisDataset:
    return DataAnalysisDataset(
        dataset_id="dataset:test",
        title="Public test metrics",
        source_locator="fixture:test",
        source_evidence_ids=["publication:test"],
        rows=[
            {"group": "a", "planned": 10, "completed": 8},
            {"group": "a", "planned": 20, "completed": 16},
            {"group": "b", "planned": 30, "completed": 27},
            {"group": "b", "planned": 40, "completed": None},
        ],
    )


def _brief() -> ResearchBrief:
    return ResearchBrief(
        project_id="project:test",
        question="Which public metric changed?",
        objective="Exercise deterministic analysis.",
        deliverables=["analysis"],
    )


def _task(*requests: DataAnalysisRequest) -> ResearchTask:
    return ResearchTask(
        task_id="task:methods",
        role=ResearchRole.METHODOLOGY_RESEARCHER,
        title="Analyze public metrics",
        instructions="Use only the configured public dataset.",
        expected_output="Auditable descriptive statistics.",
        requested_tools=[ToolCapability.DATA_ANALYSIS_SANDBOX],
        data_analysis_requests=list(requests),
    )


def test_registry_rejects_duplicate_dataset_ids() -> None:
    dataset = _dataset()

    with pytest.raises(ValueError, match="duplicate"):
        DataAnalysisDatasetRegistry([dataset, dataset])


def test_catalog_exposes_schema_but_not_rows() -> None:
    descriptor = DataAnalysisDatasetRegistry([_dataset()]).catalog()[0]

    assert descriptor.dataset_id == "dataset:test"
    assert descriptor.columns == ["completed", "group", "planned"]
    assert descriptor.row_count == 4
    assert "rows" not in type(descriptor).model_fields


def test_adapter_executes_group_mean_and_correlation() -> None:
    adapter = DataAnalysisSandboxAdapter(DataAnalysisDatasetRegistry([_dataset()]))
    task = _task(
        DataAnalysisRequest(
            request_id="group-mean",
            dataset_id="dataset:test",
            operation=DataAnalysisOperation.GROUP_MEAN,
            group_by="group",
            value_column="completed",
        ),
        DataAnalysisRequest(
            request_id="correlation",
            dataset_id="dataset:test",
            operation=DataAnalysisOperation.PEARSON_CORRELATION,
            value_column="planned",
            second_value_column="completed",
        ),
    )

    output = adapter.execute(_brief(), task)

    assert output.failures == []
    assert [item.evidence_id for item in output.evidence] == [
        "analysis:group-mean",
        "analysis:correlation",
    ]
    group_metadata = output.evidence[0].metadata
    assert group_metadata["execution_mode"] == "deterministic_allow_list"
    assert group_metadata["arbitrary_code_executed"] is False
    assert group_metadata["arbitrary_sql_executed"] is False
    assert group_metadata["source_mutated"] is False
    group_output = cast(dict[str, object], group_metadata["output"])
    groups = group_output["groups"]
    assert groups == [
        {"group": "a", "count": 2, "missing_count": 0, "mean": 12.0},
        {"group": "b", "count": 1, "missing_count": 1, "mean": 27.0},
    ]
    correlation = cast(dict[str, object], output.evidence[1].metadata["output"])
    assert correlation["paired_count"] == 3
    assert correlation["pearson_correlation"] == pytest.approx(0.9958705949)


def test_adapter_applies_numeric_filters_and_preserves_partial_success() -> None:
    adapter = DataAnalysisSandboxAdapter(DataAnalysisDatasetRegistry([_dataset()]))
    task = _task(
        DataAnalysisRequest(
            request_id="filtered-count",
            dataset_id="dataset:test",
            operation=DataAnalysisOperation.ROW_COUNT,
            filters=[
                DataAnalysisFilter(
                    column="planned",
                    operator=DataFilterOperator.GREATER_THAN_OR_EQUAL,
                    value=20,
                )
            ],
        ),
        DataAnalysisRequest(
            request_id="missing-dataset",
            dataset_id="dataset:missing",
            operation=DataAnalysisOperation.ROW_COUNT,
        ),
    )

    output = adapter.execute(_brief(), task)

    assert output.evidence[0].metadata["output"] == {"row_count": 3}
    assert len(output.failures) == 1
    assert output.failures[0].code == "dataset_not_found"


def test_invalid_column_becomes_sanitized_failure() -> None:
    adapter = DataAnalysisSandboxAdapter(DataAnalysisDatasetRegistry([_dataset()]))

    output = adapter.execute(
        _brief(),
        _task(
            DataAnalysisRequest(
                request_id="unknown-column",
                dataset_id="dataset:test",
                operation=DataAnalysisOperation.DESCRIBE_NUMERIC,
                value_column="not_present",
            )
        ),
    )

    assert output.evidence == []
    assert output.failures[0].code == "column_not_found"


def test_default_registry_is_packaged_and_traceable() -> None:
    registry = load_default_data_analysis_registry()
    descriptor = registry.catalog()[0]

    assert descriptor.dataset_id == "dataset:policy-outcomes-demo"
    assert descriptor.source_evidence_ids == ["pub:metrics"]
    assert descriptor.sensitivity == "public_only"
