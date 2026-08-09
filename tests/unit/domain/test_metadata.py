"""Metadata extraction contract tests."""

from datetime import date

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import (
    DatePrecision,
    ExtractedMetadataValue,
    ExtractedPublicationMetadata,
    ExtractionProvenance,
    MetadataEvidence,
    MetadataField,
)

PROVENANCE = ExtractionProvenance(
    parser_name="fake-pdf",
    parser_version="0.1.0",
    source_checksum="a" * 64,
)


def test_resolved_value_keeps_original_text_and_evidence_page() -> None:
    value = ExtractedMetadataValue(
        field=MetadataField.TITLE,
        normalized="미래 국방환경 연구",
        evidence=MetadataEvidence(raw_text="미래  국방환경  연구", page_number=1),
        confidence=0.9,
    )

    assert value.evidence is not None
    assert value.evidence.raw_text == "미래  국방환경  연구"
    assert value.evidence.page_number == 1


def test_missing_value_must_record_a_failure_reason() -> None:
    with pytest.raises(ValidationError, match="must record a failure_reason"):
        ExtractedMetadataValue(field=MetadataField.DOI)


def test_resolved_value_must_not_also_record_a_failure_reason() -> None:
    with pytest.raises(ValidationError, match="must not record a failure_reason"):
        ExtractedMetadataValue(
            field=MetadataField.DOI,
            normalized="10.1000/xyz",
            evidence=MetadataEvidence(raw_text="10.1000/xyz"),
            failure_reason="표지에서 찾지 못함",
        )


def test_resolved_value_must_record_evidence() -> None:
    with pytest.raises(ValidationError, match="must record evidence"):
        ExtractedMetadataValue(field=MetadataField.TITLE, normalized="제목")


def test_explicit_failure_is_a_valid_value() -> None:
    value = ExtractedMetadataValue(
        field=MetadataField.DOI,
        failure_reason="표지와 본문에 DOI 표기 없음",
    )

    assert value.normalized is None
    assert value.confidence == 0.0


def test_evidence_rejects_blank_original_text() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        MetadataEvidence(raw_text="   \n")


def test_filename_evidence_carries_no_page_number() -> None:
    evidence = MetadataEvidence(raw_text="2023_김종회_기계학습기반신속통합분석연구")

    assert evidence.page_number is None


def test_a_field_may_not_appear_twice() -> None:
    with pytest.raises(ValidationError, match="at most once"):
        ExtractedPublicationMetadata(
            publication_id="pub-1",
            provenance=PROVENANCE,
            values=[
                ExtractedMetadataValue(
                    field=MetadataField.TITLE,
                    normalized="첫 제목",
                    evidence=MetadataEvidence(raw_text="첫 제목", page_number=1),
                ),
                ExtractedMetadataValue(
                    field=MetadataField.TITLE,
                    normalized="둘째 제목",
                    evidence=MetadataEvidence(raw_text="둘째 제목", page_number=2),
                ),
            ],
        )


def test_publication_date_and_precision_must_be_set_together() -> None:
    with pytest.raises(ValidationError, match="must be set together"):
        ExtractedPublicationMetadata(
            publication_id="pub-1",
            provenance=PROVENANCE,
            publication_date=date(2023, 5, 1),
        )
    with pytest.raises(ValidationError, match="must be set together"):
        ExtractedPublicationMetadata(
            publication_id="pub-1",
            provenance=PROVENANCE,
            date_precision=DatePrecision.YEAR,
        )


def test_year_only_date_is_expressible() -> None:
    metadata = ExtractedPublicationMetadata(
        publication_id="pub-1",
        provenance=PROVENANCE,
        publication_date=date(2023, 1, 1),
        date_precision=DatePrecision.YEAR,
    )

    assert metadata.date_precision is DatePrecision.YEAR


def test_metadata_round_trips_through_json() -> None:
    metadata = ExtractedPublicationMetadata(
        publication_id="pub-1",
        provenance=PROVENANCE,
        values=[
            ExtractedMetadataValue(
                field=MetadataField.ORGANIZATION,
                normalized="한국국방연구원",
                evidence=MetadataEvidence(raw_text="한국국방연구원", page_number=1),
                confidence=0.8,
            )
        ],
    )

    restored = ExtractedPublicationMetadata.model_validate_json(metadata.model_dump_json())

    assert restored == metadata
