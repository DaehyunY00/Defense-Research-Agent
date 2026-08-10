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

REPORT_IDENTIFIER_FIRST_COVER = """\
연구보고서 인력2024-5240
2024. 12.
군 간부 전체 계급에 대한
정년 연구
안석기 정관영 박민섭 한서영 김푸름
"""

REPORT_DATE_LAST_COVER = """\
연구보고서 군사
기계학습 기반 신속통합분석 연구
김종회, 심광신, 박효린, 서민혁, 손병원
2023. 11.
"""

JOURNAL_TITLE_NOTE_COVER = """\
국방정책연구 2025년 여름(41-2) 통권 제148호 pp. 235-262
http://dx.doi.org/10.22883/jdps.2025.41.2.008
ISSN 1598-6101(print), 2672-1392(online)
DEA-SBM을 적용한 육군 군수부대의
운영효율성 분석†
1)
주명희*, 손대권**, 하헌구***
I. 서론
"""

JOURNAL_ATTACHED_NOTE_AUTHOR_COVER = """\
국방정책연구 2025년 여름(41-2) 통권 제148호 pp. 137-169
http://dx.doi.org/10.22883/jdps.2025.41.2.005
ISSN 1598-6101(print), 2672-1392(online)
한국군의 적정 상비병력 규모에 관한 연구:
현대 전쟁사례의 최소계획비율을 중심으로†
1)2)김정혁*, 지효근**
I. 서론
"""

BRIEF_AUTHOR_COVER = """\
김의순 책임연구위원, 홍수민 전문연구원
군사발전연구센터
"""

BRIEF_TITLE_COVER = """\
배경과 목적
수행 결과
KIDA Brief 1
미국의 3차 상쇄전략 추진 동향과 시사점
미국의 3차 상쇄전략 추진 동향과 시사점
강석율
안보전략연구센터
"""

BRIEF_BODY_WITH_UNRELATED_DATE = """\
2018년 1월에 공개된 국방전략서는 새로운 안보 환경을 설명했다.
이 문장은 해당 Brief의 발행일을 나타내지 않는다.
"""

REPORT_DASH_SUBTITLE_COVER = """\
연구보고서 군사
미래우주전에 대한 고찰
- 이론과 위협을 중심으로 -
조홍일, 유기현, 김성학, 전재현, 이경혜
2023. 10.
"""

REPORT_SINGLE_AUTHOR_COVER = """\
2023. 9.
연구보고서 안보2023-4946
ISBN 97859784
핵보유국의 핵 정책 비교연구
박상현
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


def test_journal_title_removes_inline_symbol_and_standalone_note_number() -> None:
    metadata = _extract(
        PublicationType.DEFENSE_POLICY_RESEARCH,
        [_page(JOURNAL_TITLE_NOTE_COVER)],
    )

    title = _value(metadata, MetadataField.TITLE)
    assert title.normalized == "DEA-SBM을 적용한 육군 군수부대의 운영효율성 분석"
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.COVER_PAGE
    assert title.evidence.raw_text.endswith("분석†\n1)")


def test_journal_title_stops_at_author_line_with_leading_note_numbers() -> None:
    metadata = _extract(
        PublicationType.DEFENSE_POLICY_RESEARCH,
        [_page(JOURNAL_ATTACHED_NOTE_AUTHOR_COVER)],
    )

    assert _value(metadata, MetadataField.TITLE).normalized == (
        "한국군의 적정 상비병력 규모에 관한 연구"
    )
    assert _value(metadata, MetadataField.SUBTITLE).normalized == (
        "현대 전쟁사례의 최소계획비율을 중심으로"
    )
    assert [author.name for author in metadata.authors] == ["김정혁", "지효근"]


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


def test_brief_body_historical_date_is_not_promoted_to_publication_date() -> None:
    metadata = _extract(
        PublicationType.KIDA_BRIEF,
        [
            _page(BRIEF_TITLE_COVER),
            _page(BRIEF_BODY_WITH_UNRELATED_DATE, page_number=2),
        ],
        Path("2022_강석율_미국의3차상쇄전략추진동향과시사점.pdf"),
    )

    assert metadata.dates.filename_year == 2022
    assert metadata.dates.published_at is None
    assert metadata.dates.published_precision is None
    assert metadata.dates.date_evidence is None
    assert metadata.dates.failure_reason == (
        "표지 발행일이 없고 본문 날짜는 발행일 근거로 사용할 수 없음"
    )


def test_conflicting_cover_dates_return_none_with_a_failure_reason() -> None:
    cover = "발행 2024. 1.\n발행 2025. 2."
    metadata = _extract(PublicationType.OTHER, [_page(cover)])

    assert metadata.dates.published_at is None
    assert metadata.dates.published_precision is None
    assert metadata.dates.date_evidence is None
    assert metadata.dates.failure_reason == "동일한 우선순위의 발행일 근거가 충돌함"


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


def test_report_identifier_first_layout_extracts_title_after_date() -> None:
    metadata = _extract(
        PublicationType.RESEARCH_REPORT,
        [_page(REPORT_IDENTIFIER_FIRST_COVER)],
        Path("2024_안석기_군간부전체계급에대한정년연구.pdf"),
    )

    title = _value(metadata, MetadataField.TITLE)
    assert title.normalized == "군 간부 전체 계급에 대한 정년 연구"
    assert title.confidence == 0.96
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.COVER_PAGE
    assert title.evidence.page_number == 1
    assert title.evidence.raw_text == "군 간부 전체 계급에 대한\n정년 연구"


def test_report_identifier_first_layout_stops_title_before_authors() -> None:
    metadata = _extract(
        PublicationType.RESEARCH_REPORT,
        [_page(REPORT_DATE_LAST_COVER)],
        Path("2023_김종회_기계학습기반신속통합분석연구.pdf"),
    )

    title = _value(metadata, MetadataField.TITLE)
    assert title.normalized == "기계학습 기반 신속통합분석 연구"
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.COVER_PAGE
    assert title.evidence.raw_text == "기계학습 기반 신속통합분석 연구"


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


def test_brief_cover_boilerplate_is_excluded_and_duplicate_title_is_folded() -> None:
    metadata = _extract(PublicationType.KIDA_BRIEF, [_page(BRIEF_TITLE_COVER)])

    title = _value(metadata, MetadataField.TITLE)
    assert title.normalized == "미국의 3차 상쇄전략 추진 동향과 시사점"
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.COVER_PAGE
    assert title.evidence.page_number == 1
    assert title.evidence.raw_text == "미국의 3차 상쇄전략 추진 동향과 시사점"
    assert metadata.authors[0].name == "강석율"


def test_report_dash_delimited_subtitle_is_extracted_separately() -> None:
    metadata = _extract(PublicationType.RESEARCH_REPORT, [_page(REPORT_DASH_SUBTITLE_COVER)])

    title = _value(metadata, MetadataField.TITLE)
    subtitle = _value(metadata, MetadataField.SUBTITLE)
    assert title.normalized == "미래우주전에 대한 고찰"
    assert subtitle.normalized == "이론과 위협을 중심으로"
    assert title.evidence is not None
    assert subtitle.evidence is not None
    assert title.evidence.raw_text == subtitle.evidence.raw_text
    assert "- 이론과 위협을 중심으로 -" in subtitle.evidence.raw_text


def test_report_single_author_is_a_boundary_without_source_path() -> None:
    metadata = _extract(PublicationType.RESEARCH_REPORT, [_page(REPORT_SINGLE_AUTHOR_COVER)])

    title = _value(metadata, MetadataField.TITLE)
    assert title.normalized == "핵보유국의 핵 정책 비교연구"
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.COVER_PAGE
    assert [author.name for author in metadata.authors] == ["박상현"]
    assert metadata.authors[0].evidence is not None
    assert metadata.authors[0].evidence.source is MetadataEvidenceSource.COVER_PAGE


def test_report_single_word_title_is_not_guessed_as_an_author() -> None:
    cover = "연구보고서 군사2026-1\n인공지능\n2026. 1."
    metadata = _extract(PublicationType.RESEARCH_REPORT, [_page(cover)])

    assert _value(metadata, MetadataField.TITLE).normalized == "인공지능"
    assert metadata.authors[0].name is None
    assert metadata.authors[0].failure_reason == "저자 근거를 찾을 수 없음"


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


def test_report_spaced_summary_heading_extracts_body_abstract() -> None:
    body = """\
요 약
본문 페이지에서 명시적으로 제공된 요약이다.
I. 서 론
후속 절의 본문은 요약에 포함되지 않는다.
"""
    metadata = _extract(
        PublicationType.RESEARCH_REPORT,
        [_page(REPORT_COVER), _page(body, page_number=2)],
    )

    abstract = _value(metadata, MetadataField.ABSTRACT)
    assert abstract.normalized == "본문 페이지에서 명시적으로 제공된 요약이다."
    assert abstract.evidence is not None
    assert abstract.evidence.source is MetadataEvidenceSource.BODY
    assert abstract.evidence.page_number == 2


def test_multiline_keywords_stop_before_periodical_running_header() -> None:
    body = """\
Key words: cognitive warfare, cognitive psychology, brain science, cognitive science,
narrative, propaganda
국방정책연구 제39권 제3호・2023년 가을(통권 제141호)
"""
    metadata = _extract(
        PublicationType.DEFENSE_POLICY_RESEARCH,
        [_page(body, page_number=2)],
    )

    keywords = [value for value in metadata.values if value.field is MetadataField.KEYWORDS]
    assert [value.normalized for value in keywords] == [
        "cognitive warfare",
        "cognitive psychology",
        "brain science",
        "cognitive science",
        "narrative",
        "propaganda",
    ]
    assert all(
        value.evidence is not None and value.evidence.source is MetadataEvidenceSource.BODY
        for value in keywords
    )
    assert all(
        value.evidence is not None and "국방정책연구" not in value.evidence.raw_text
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
    assert metadata.dates.date_evidence is None
    assert metadata.dates.failure_reason == "표지에서 발행일 근거를 찾을 수 없음"


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
