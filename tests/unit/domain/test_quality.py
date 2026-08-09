"""Quality gate contract tests."""

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import (
    PublicationQualityStatus,
    PublicationQualityVerdict,
    QualityMeasurements,
    QualityThresholds,
)


def _measurements(**overrides: object) -> QualityMeasurements:
    values: dict[str, object] = {
        "character_count": 5_000,
        "page_count": 10,
        "non_empty_page_count": 9,
        "control_character_count": 0,
        "printable_ratio": 0.99,
        "korean_ratio": 0.7,
    }
    values.update(overrides)
    return QualityMeasurements.model_validate(values)


def test_only_ready_and_warning_are_indexable() -> None:
    indexable = {status for status in PublicationQualityStatus if status.is_indexable}

    assert indexable == {
        PublicationQualityStatus.READY,
        PublicationQualityStatus.WARNING,
    }


def test_non_empty_pages_may_not_exceed_total_pages() -> None:
    with pytest.raises(ValidationError, match="must not exceed page_count"):
        _measurements(page_count=3, non_empty_page_count=4)


def test_ratio_fields_reject_values_outside_the_unit_interval() -> None:
    with pytest.raises(ValidationError):
        _measurements(printable_ratio=1.2)
    with pytest.raises(ValidationError):
        _measurements(korean_ratio=-0.1)


def test_zero_page_publication_is_accepted_as_a_measurement() -> None:
    measurements = _measurements(page_count=0, non_empty_page_count=0, character_count=0)

    assert measurements.page_count == 0


def test_ready_verdict_needs_no_reason() -> None:
    verdict = PublicationQualityVerdict(
        publication_id="pub-1",
        status=PublicationQualityStatus.READY,
        measurements=_measurements(),
        thresholds_version="quality-v1",
    )

    assert verdict.reasons == []


def test_non_ready_verdict_must_record_a_reason() -> None:
    with pytest.raises(ValidationError, match="at least one reason"):
        PublicationQualityVerdict(
            publication_id="pub-1",
            status=PublicationQualityStatus.LOW_TEXT,
            measurements=_measurements(character_count=10),
            thresholds_version="quality-v1",
        )


def test_duplicate_verdict_must_name_the_publication_it_duplicates() -> None:
    with pytest.raises(ValidationError, match="must record duplicate_of"):
        PublicationQualityVerdict(
            publication_id="pub-1",
            status=PublicationQualityStatus.DUPLICATE,
            measurements=_measurements(),
            thresholds_version="quality-v1",
            reasons=["동일 본문 checksum"],
        )


def test_duplicate_of_is_rejected_on_a_non_duplicate_verdict() -> None:
    with pytest.raises(ValidationError, match="only valid for a duplicate"):
        PublicationQualityVerdict(
            publication_id="pub-1",
            status=PublicationQualityStatus.WARNING,
            measurements=_measurements(),
            thresholds_version="quality-v1",
            reasons=["표 추출 누락"],
            duplicate_of="pub-2",
        )


def test_thresholds_carry_a_version_and_survive_round_trip() -> None:
    thresholds = QualityThresholds(thresholds_version="quality-v1")

    restored = QualityThresholds.model_validate_json(thresholds.model_dump_json())

    assert restored == thresholds
    assert restored.thresholds_version == "quality-v1"


def test_thresholds_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        QualityThresholds.model_validate(
            {"thresholds_version": "quality-v1", "min_english_ratio": 0.5}
        )
