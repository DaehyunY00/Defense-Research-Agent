"""Metadata extraction contract tests.

Fixtures mirror shapes recorded in ``docs/DATA_QUALITY_REPORT.md``: seasonal
issue statements from ``국방정책연구``, file-name years that disagree with the
body, and covers carrying several authors with footnote-linked affiliations.
"""

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from defense_research_agent.domain import (
    DatePrecision,
    ExtractedAuthor,
    ExtractedMetadataValue,
    ExtractedPublicationMetadata,
    ExtractionProvenance,
    MetadataEvidence,
    MetadataEvidenceSource,
    MetadataField,
    PublicationDates,
)

PROVENANCE = ExtractionProvenance(
    parser_name="fake-pdf",
    parser_version="0.1.0",
    source_checksum="a" * 64,
)


def _cover(raw_text: str, page_number: int = 1) -> MetadataEvidence:
    return MetadataEvidence(
        source=MetadataEvidenceSource.COVER_PAGE,
        raw_text=raw_text,
        page_number=page_number,
    )


def _filename(raw_text: str) -> MetadataEvidence:
    return MetadataEvidence(source=MetadataEvidenceSource.FILENAME, raw_text=raw_text)


def test_resolved_value_keeps_original_text_and_evidence_page() -> None:
    value = ExtractedMetadataValue(
        field=MetadataField.TITLE,
        normalized="미래 국방환경 연구",
        evidence=_cover("미래  국방환경  연구"),
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
            evidence=_cover("10.1000/xyz"),
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


def test_authors_may_not_be_recorded_as_a_flat_value() -> None:
    with pytest.raises(ValidationError, match="structured value"):
        ExtractedMetadataValue(
            field=MetadataField.AUTHORS,
            normalized="김종회, 오혜",
            evidence=_cover("김종회, 오혜"),
        )


# --- evidence source precedence -------------------------------------------------


def test_cover_page_evidence_outranks_filename_evidence() -> None:
    cover = _cover("국방분야 실행 아키텍처 구현방안 연구")
    filename = _filename("2019_김의순_국방분야실행아키텍처구현방안연구")

    assert cover.strength > filename.strength


def test_document_backed_evidence_must_record_a_page() -> None:
    with pytest.raises(ValidationError, match="must record a page_number"):
        MetadataEvidence(source=MetadataEvidenceSource.COVER_PAGE, raw_text="제목")


def test_filename_evidence_must_not_claim_a_page() -> None:
    with pytest.raises(ValidationError, match="must not record a page_number"):
        MetadataEvidence(
            source=MetadataEvidenceSource.FILENAME,
            raw_text="2019_김의순_보고서",
            page_number=1,
        )


def test_evidence_rejects_blank_original_text() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        MetadataEvidence(source=MetadataEvidenceSource.FILENAME, raw_text="   \n")


# --- multi-valued and structured fields -----------------------------------------


def test_keywords_may_repeat_with_distinct_ordinals() -> None:
    metadata = ExtractedPublicationMetadata(
        publication_id="pub-1",
        provenance=PROVENANCE,
        values=[
            ExtractedMetadataValue(
                field=MetadataField.KEYWORDS,
                ordinal=index,
                normalized=keyword,
                evidence=_cover(keyword, page_number=2),
            )
            for index, keyword in enumerate(["킬체인", "KAMD", "국방혁신"])
        ],
    )

    assert [value.normalized for value in metadata.values] == ["킬체인", "KAMD", "국방혁신"]


def test_single_valued_field_may_not_use_a_nonzero_ordinal() -> None:
    with pytest.raises(ValidationError, match="single-valued"):
        ExtractedMetadataValue(
            field=MetadataField.TITLE,
            ordinal=1,
            normalized="둘째 제목",
            evidence=_cover("둘째 제목"),
        )


def test_the_same_field_and_ordinal_may_not_repeat() -> None:
    with pytest.raises(ValidationError, match="at most once"):
        ExtractedPublicationMetadata(
            publication_id="pub-1",
            provenance=PROVENANCE,
            values=[
                ExtractedMetadataValue(
                    field=MetadataField.KEYWORDS,
                    ordinal=0,
                    normalized="킬체인",
                    evidence=_cover("킬체인", page_number=2),
                ),
                ExtractedMetadataValue(
                    field=MetadataField.KEYWORDS,
                    ordinal=0,
                    normalized="KAMD",
                    evidence=_cover("KAMD", page_number=2),
                ),
            ],
        )


def test_multiple_authors_keep_role_affiliation_and_per_author_evidence() -> None:
    metadata = ExtractedPublicationMetadata(
        publication_id="pub-1",
        provenance=PROVENANCE,
        authors=[
            ExtractedAuthor(
                ordinal=0,
                name="김의순",
                role="선임연구위원",
                affiliation="한국국방연구원 국방정책연구실",
                email="kim@example.re.kr",
                is_primary=True,
                evidence=_cover("김의순* 선임연구위원"),
                confidence=0.9,
            ),
            ExtractedAuthor(
                ordinal=1,
                name="오혜",
                role="연구원",
                affiliation="한국국방연구원 인력연구실",
                evidence=_cover("오혜** 연구원"),
                confidence=0.7,
            ),
        ],
    )

    assert [author.name for author in metadata.authors] == ["김의순", "오혜"]
    assert metadata.authors[0].affiliation != metadata.authors[1].affiliation
    assert metadata.authors[1].email is None


def test_unresolved_author_is_an_explicit_failure_not_a_guess() -> None:
    author = ExtractedAuthor(
        ordinal=0,
        failure_reason="표지 저자 구간에서 이름을 확정하지 못함",
    )

    assert author.name is None


def test_resolved_author_must_record_evidence() -> None:
    with pytest.raises(ValidationError, match="must record evidence"):
        ExtractedAuthor(ordinal=0, name="김의순")


def test_author_ordinal_must_not_repeat() -> None:
    with pytest.raises(ValidationError, match="ordinal must not repeat"):
        ExtractedPublicationMetadata(
            publication_id="pub-1",
            provenance=PROVENANCE,
            authors=[
                ExtractedAuthor(ordinal=0, name="김의순", evidence=_cover("김의순")),
                ExtractedAuthor(ordinal=0, name="오혜", evidence=_cover("오혜")),
            ],
        )


def test_only_one_author_may_be_primary() -> None:
    with pytest.raises(ValidationError, match="at most one author"):
        ExtractedPublicationMetadata(
            publication_id="pub-1",
            provenance=PROVENANCE,
            authors=[
                ExtractedAuthor(
                    ordinal=0, name="김의순", is_primary=True, evidence=_cover("김의순")
                ),
                ExtractedAuthor(ordinal=1, name="오혜", is_primary=True, evidence=_cover("오혜")),
            ],
        )


# --- dates ----------------------------------------------------------------------


def test_the_three_dates_are_preserved_separately() -> None:
    dates = PublicationDates(
        filename_year=2024,
        published_at=date(2024, 11, 4),
        published_precision=DatePrecision.DAY,
        processed_at=datetime(2026, 2, 2, 23, 30, 6),
        date_evidence=_cover("2024년 11월 4일", page_number=1),
    )

    assert dates.filename_year == 2024
    assert dates.published_at == date(2024, 11, 4)
    assert dates.processed_at is not None
    assert not dates.has_year_conflict


def test_filename_year_disagreeing_with_the_body_is_reported_not_overwritten() -> None:
    dates = PublicationDates(
        filename_year=2024,
        published_at=date(2023, 12, 20),
        published_precision=DatePrecision.DAY,
        date_evidence=_cover("2023년 12월 20일", page_number=1),
    )

    assert dates.has_year_conflict
    assert dates.filename_year == 2024
    assert dates.published_at is not None
    assert dates.published_at.year == 2023


def test_seasonal_issue_is_expressible_and_keeps_its_original_wording() -> None:
    dates = PublicationDates(
        published_at=date(2024, 6, 1),
        published_precision=DatePrecision.SEASON,
        issue_label="2024년 여름(40-2)",
        date_evidence=_cover("2024년 여름(40-2)", page_number=1),
    )

    assert dates.published_precision is DatePrecision.SEASON
    assert dates.issue_label == "2024년 여름(40-2)"


def test_season_precision_without_the_original_wording_is_rejected() -> None:
    with pytest.raises(ValidationError, match="original issue_label"):
        PublicationDates(
            published_at=date(2024, 6, 1),
            published_precision=DatePrecision.SEASON,
            date_evidence=_cover("2024년 여름", page_number=1),
        )


def test_publication_date_and_precision_must_be_set_together() -> None:
    with pytest.raises(ValidationError, match="must be set together"):
        PublicationDates(published_at=date(2023, 5, 1))
    with pytest.raises(ValidationError, match="must be set together"):
        PublicationDates(published_precision=DatePrecision.YEAR)


def test_resolved_date_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="must record date_evidence"):
        PublicationDates(published_at=date(2023, 5, 1), published_precision=DatePrecision.YEAR)


def test_a_year_only_filename_reading_alone_is_not_a_conflict() -> None:
    dates = PublicationDates(filename_year=2024)

    assert not dates.has_year_conflict


def test_filename_year_outside_the_plausible_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PublicationDates(filename_year=1799)


def test_metadata_round_trips_through_json() -> None:
    metadata = ExtractedPublicationMetadata(
        publication_id="pub-1",
        provenance=PROVENANCE,
        values=[
            ExtractedMetadataValue(
                field=MetadataField.ORGANIZATION,
                normalized="한국국방연구원",
                evidence=_cover("한국국방연구원"),
                confidence=0.8,
            )
        ],
        authors=[ExtractedAuthor(ordinal=0, name="김의순", evidence=_cover("김의순"))],
        dates=PublicationDates(filename_year=2019),
    )

    restored = ExtractedPublicationMetadata.model_validate_json(metadata.model_dump_json())

    assert restored == metadata
