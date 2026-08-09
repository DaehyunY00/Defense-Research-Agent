"""Regression tests for the deterministic KIDA metadata extractor.

The text shapes reproduce observed pages documented in DATA_QUALITY_REPORT.md:
control-separated 국방논단 covers, seasonal 국방정책연구 headers and author
footnotes, report month covers, and author-only Brief cover extraction.
"""

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from defense_research_agent.domain.metadata import (
    DatePrecision,
    ExtractedMetadataValue,
    ExtractedPublicationMetadata,
    MetadataEvidenceSource,
    MetadataField,
)
from defense_research_agent.domain.provenance import ExtractionProvenance
from defense_research_agent.domain.publication import (
    PublicationPage,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.search.metadata import (
    METADATA_NORMALIZATION_VERSION,
    RuleBasedPublicationMetadataExtractor,
    normalize_metadata_text,
)

SOURCE_CHECKSUM = "a" * 64
PAGE_PROVENANCE = ExtractionProvenance(
    parser_name="fixture-parser",
    parser_version="1.0.0",
    source_checksum=SOURCE_CHECKSUM,
)
PROCESSED_AT = datetime(2026, 2, 2, 23, 30, 6, 937817)

TRUNCATED_FILENAME = Path("2019_김의순_국방분야실행아키텍처구현방안연ㄱ.pdf")

FORUM_COVER = """\
제1983호(24-10)\x01 2024년\x01 3월\x01 15일
발행처\x01 한국국방연구원
발행인\x01 탁성한
편집인\x01 이재욱
초급간부\x01 선발\x01 시, AI\x01 면접의\x01 발전방향
곽지희
한국국방연구원\x01 국방인력연구센터
jiheekwak@kida.re.kr
"""

JOURNAL_COVER = """\
국방정책연구 2024년 여름(40-2) 통권 제144호 pp. 93-132
http://dx.doi.org/10.22883/jdps.2024.40.2.004
ISSN 1598-6101(print), 2672-1392(online)
미래전에 대비한 한국군 인지전 발전 방향:
인지전 개념, 전개 양상, 그리고 전략적 대응
김정원*, 이혜원**1)
I. 서론
* 제1저자, 공군 소령, 연세대학교 심리학과 석사과정, sue6511@gmail.com
** 제2저자, 연세대학교, 심리학 박사, jesshyewon@gmail.com
Abstract
The Direction of Korean Military Cognitive Warfare
 This study presents a deterministic fixture paragraph grounded in the observed format.
Key words: cognitive warfare, cognitive psychology, narrative
"""

REPORT_COVER = """\
단기복무 간부 획득을 위한
정책 발전방향
2023. 10.
한국국방연구원
"""

BRIEF_AUTHOR_COVER = """\
김의순 책임연구위원, 홍수민 전문연구원
군사발전연구센터
"""


def _publication(
    publication_type: PublicationType,
    *,
    publication_id: str = "pub-1",
    processed_at: datetime | None = PROCESSED_AT,
) -> ResearchPublication:
    return ResearchPublication(
        publication_id=publication_id,
        publication_type=publication_type,
        created_at=processed_at,
        checksum=SOURCE_CHECKSUM,
    )


def _page(text: str, page_number: int = 1) -> PublicationPage:
    return PublicationPage(
        page_number=page_number,
        text=text,
        provenance=PAGE_PROVENANCE,
    )


def _value(
    metadata: ExtractedPublicationMetadata,
    field: MetadataField,
    ordinal: int = 0,
) -> ExtractedMetadataValue:
    return next(
        value for value in metadata.values if value.field is field and value.ordinal == ordinal
    )


def _extract(
    publication_type: PublicationType,
    pages: Sequence[PublicationPage],
    source_path: Path | None = None,
) -> ExtractedPublicationMetadata:
    return RuleBasedPublicationMetadataExtractor().extract(
        _publication(publication_type),
        pages,
        source_path,
    )


def test_cover_wins_and_discards_a_conflicting_truncated_filename() -> None:
    metadata = _extract(
        PublicationType.DEFENSE_FORUM,
        [_page(FORUM_COVER)],
        TRUNCATED_FILENAME,
    )

    title = _value(metadata, MetadataField.TITLE)
    assert title.normalized == "초급간부 선발 시, AI 면접의 발전방향"
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.COVER_PAGE
    assert title.evidence.page_number == 1
    assert "\x01" in title.evidence.raw_text
    assert len([value for value in metadata.values if value.field is MetadataField.TITLE]) == 1
    assert "연ㄱ" not in title.normalized


def test_filename_is_low_confidence_evidence_when_cover_is_absent() -> None:
    metadata = _extract(PublicationType.KIDA_BRIEF, [], TRUNCATED_FILENAME)

    title = _value(metadata, MetadataField.TITLE)
    assert title.normalized == "국방분야실행아키텍처구현방안연ㄱ"
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.FILENAME
    assert title.confidence < 0.3
    assert metadata.authors[0].name == "김의순"
    assert metadata.authors[0].evidence is not None
    assert metadata.authors[0].evidence.source is MetadataEvidenceSource.FILENAME


def test_journal_extracts_structured_authors_footnotes_and_bibliography() -> None:
    metadata = _extract(
        PublicationType.DEFENSE_POLICY_RESEARCH,
        [_page(JOURNAL_COVER)],
        Path("2023_김정원_잘린파일명ㄱ.pdf"),
    )

    assert _value(metadata, MetadataField.TITLE).normalized == (
        "미래전에 대비한 한국군 인지전 발전 방향"
    )
    assert _value(metadata, MetadataField.SUBTITLE).normalized == (
        "인지전 개념, 전개 양상, 그리고 전략적 대응"
    )
    assert _value(metadata, MetadataField.VOLUME).normalized == "40"
    assert _value(metadata, MetadataField.ISSUE_NUMBER).normalized == "2"
    assert _value(metadata, MetadataField.DOI).normalized == "10.22883/jdps.2024.40.2.004"
    assert _value(metadata, MetadataField.ABSTRACT).normalized == (
        "This study presents a deterministic fixture paragraph grounded in the observed format."
    )
    assert [
        value.normalized for value in metadata.values if value.field is MetadataField.KEYWORDS
    ] == ["cognitive warfare", "cognitive psychology", "narrative"]

    assert [author.name for author in metadata.authors] == ["김정원", "이혜원"]
    first, second = metadata.authors
    assert first.is_primary
    assert not second.is_primary
    assert first.role == "제1저자, 공군 소령"
    assert first.affiliation == "연세대학교 심리학과 석사과정"
    assert first.email == "sue6511@gmail.com"
    assert second.role == "제2저자, 심리학 박사"
    assert second.affiliation == "연세대학교"
    assert second.email == "jesshyewon@gmail.com"
    assert all(author.confidence > 0.9 for author in metadata.authors)
    assert all(
        author.evidence is not None and author.evidence.source is MetadataEvidenceSource.COVER_PAGE
        for author in metadata.authors
    )


def test_filename_and_published_years_are_both_preserved_as_a_conflict() -> None:
    metadata = _extract(
        PublicationType.DEFENSE_POLICY_RESEARCH,
        [_page(JOURNAL_COVER)],
        Path("2023_김정원_미래전에대비한한국군인지전발전방향.pdf"),
    )

    assert metadata.dates.filename_year == 2023
    assert metadata.dates.published_at == date(2024, 6, 1)
    assert metadata.dates.has_year_conflict
    assert metadata.dates.date_evidence is not None
    assert metadata.dates.date_evidence.source is MetadataEvidenceSource.COVER_PAGE


def test_brief_business_year_does_not_replace_the_published_month() -> None:
    cover = "2019 사업연도 연구\n발행 2020. 7.\n김의순 책임연구위원\n군사발전연구센터"
    metadata = _extract(
        PublicationType.KIDA_BRIEF,
        [_page(cover)],
        Path("2019_김의순_국방분야실행아키텍처구현방안연구.pdf"),
    )

    assert metadata.dates.filename_year == 2019
    assert metadata.dates.published_at == date(2020, 7, 1)
    assert metadata.dates.published_precision is DatePrecision.MONTH
    assert metadata.dates.has_year_conflict


def test_season_precision_and_original_issue_label_are_preserved() -> None:
    metadata = _extract(
        PublicationType.DEFENSE_POLICY_RESEARCH,
        [_page(JOURNAL_COVER)],
    )

    assert metadata.dates.published_precision is DatePrecision.SEASON
    assert metadata.dates.issue_label == "2024년 여름(40-2)"


def test_forum_day_report_month_and_processed_time_stay_separate() -> None:
    forum = _extract(PublicationType.DEFENSE_FORUM, [_page(FORUM_COVER)])
    report = _extract(PublicationType.RESEARCH_REPORT, [_page(REPORT_COVER)])

    assert forum.dates.published_at == date(2024, 3, 15)
    assert forum.dates.published_precision is DatePrecision.DAY
    assert forum.dates.processed_at == PROCESSED_AT
    assert _value(forum, MetadataField.ORGANIZATION).normalized == "한국국방연구원"
    assert _value(forum, MetadataField.ISSUE_NUMBER).normalized == "1983"
    assert forum.authors[0].name == "곽지희"
    assert forum.authors[0].affiliation == "한국국방연구원 국방인력연구센터"
    assert forum.authors[0].email == "jiheekwak@kida.re.kr"

    assert report.dates.published_at == date(2023, 10, 1)
    assert report.dates.published_precision is DatePrecision.MONTH
    assert report.dates.processed_at == PROCESSED_AT
    assert _value(report, MetadataField.TITLE).normalized == (
        "단기복무 간부 획득을 위한 정책 발전방향"
    )


def test_brief_multiple_role_authors_are_not_mistaken_for_a_title() -> None:
    metadata = _extract(
        PublicationType.KIDA_BRIEF,
        [_page(BRIEF_AUTHOR_COVER)],
        Path("2019_김의순_국방분야실행아키텍처구현방안연구.pdf"),
    )

    assert [author.name for author in metadata.authors] == ["김의순", "홍수민"]
    assert [author.role for author in metadata.authors] == ["책임연구위원", "전문연구원"]
    assert all(author.affiliation == "군사발전연구센터" for author in metadata.authors)
    title = _value(metadata, MetadataField.TITLE)
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.FILENAME


def test_report_body_terms_are_not_guessed_as_authors_or_issue_numbers() -> None:
    body = "연구 수행 과정\n육군사관학교\n관련 규정 제3호"
    metadata = _extract(
        PublicationType.RESEARCH_REPORT,
        [_page(REPORT_COVER), _page(body, page_number=2)],
        Path("2023_권현진_단기복무간부획득을위한정책발전방향.pdf"),
    )

    assert metadata.authors[0].name == "권현진"
    assert metadata.authors[0].evidence is not None
    assert metadata.authors[0].evidence.source is MetadataEvidenceSource.FILENAME
    assert _value(metadata, MetadataField.ISSUE_NUMBER).normalized is None
    assert metadata.dates.issue_label is None


def test_body_evidence_is_distinguished_from_cover_and_filename() -> None:
    body = """\
초록
본문 페이지에서 명시적으로 제공된 초록이다.
주제어: 국방정책, 메타데이터
"""
    metadata = _extract(
        PublicationType.RESEARCH_REPORT,
        [_page(REPORT_COVER), _page(body, page_number=2)],
    )

    abstract = _value(metadata, MetadataField.ABSTRACT)
    assert abstract.evidence is not None
    assert abstract.evidence.source is MetadataEvidenceSource.BODY
    assert abstract.evidence.page_number == 2
    keywords = [value for value in metadata.values if value.field is MetadataField.KEYWORDS]
    assert [value.normalized for value in keywords] == ["국방정책", "메타데이터"]
    assert all(
        value.evidence is not None and value.evidence.source is MetadataEvidenceSource.BODY
        for value in keywords
    )


def test_unresolved_fields_return_none_with_failure_reasons() -> None:
    metadata = _extract(PublicationType.OTHER, [])

    assert len(metadata.values) == 8
    assert all(value.normalized is None for value in metadata.values)
    assert all(value.failure_reason for value in metadata.values)
    assert metadata.authors[0].name is None
    assert metadata.authors[0].failure_reason
    assert metadata.dates.published_at is None
    assert metadata.dates.published_precision is None


def test_equal_strength_conflict_is_not_guessed() -> None:
    cover = """\
연구 제목
https://doi.org/10.1000/first
https://doi.org/10.1000/second
"""
    metadata = _extract(PublicationType.OTHER, [_page(cover)])

    doi = _value(metadata, MetadataField.DOI)
    assert doi.normalized is None
    assert doi.failure_reason == "동일한 우선순위의 메타데이터 근거가 충돌함"


def test_same_input_is_byte_identical_and_normalization_is_versioned() -> None:
    publication = _publication(PublicationType.DEFENSE_POLICY_RESEARCH)
    pages = [_page(JOURNAL_COVER)]
    source_path = Path("2023_김정원_미래전에대비한한국군인지전발전방향.pdf")
    extractor = RuleBasedPublicationMetadataExtractor()

    first = extractor.extract(publication, pages, source_path)
    second = extractor.extract(publication, pages, source_path)

    assert first.model_dump_json().encode("utf-8") == second.model_dump_json().encode("utf-8")
    assert METADATA_NORMALIZATION_VERSION == "nfc-whitespace-v1"
    assert normalize_metadata_text("국방\x01  정책") == "국방 정책"


def test_filename_evidence_preserves_nfd_while_value_is_nfc() -> None:
    source_path = Path("2023_김의순_국방정책.pdf")
    metadata = _extract(PublicationType.KIDA_BRIEF, [], source_path)

    title = _value(metadata, MetadataField.TITLE)
    assert title.normalized == "국방정책"
    assert title.evidence is not None
    assert title.evidence.raw_text == "국방정책"
    assert metadata.authors[0].name == "김의순"
    assert metadata.authors[0].evidence is not None
    assert metadata.authors[0].evidence.raw_text == "김의순"
