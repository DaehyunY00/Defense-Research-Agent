"""Metadata extractor interface test, exercised through a fake extractor.

The fake implements the cover-over-filename precedence rule the contract
documents, so the rule is actually executed rather than only described. File
names follow the corpus convention ``<year>_<author>_<title>.pdf`` and include
the truncation case ``DATA_QUALITY_REPORT.md`` DQ-04 records.
"""

from collections.abc import Sequence
from datetime import date
from pathlib import Path

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
    PublicationPage,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.search import PublicationMetadataExtractor

SOURCE_CHECKSUM = "e" * 64
PAGE_PROVENANCE = ExtractionProvenance(
    parser_name="fake-page-parser",
    parser_version="1.0.0",
    source_checksum=SOURCE_CHECKSUM,
)


def _page(text: str) -> PublicationPage:
    return PublicationPage(page_number=1, text=text, provenance=PAGE_PROVENANCE)


def _parse_filename(source_path: Path) -> tuple[int | None, str | None, str | None]:
    """Split ``<year>_<author>_<title>`` without guessing missing parts."""
    parts = source_path.stem.split("_")
    if len(parts) < 3 or not parts[0].isdigit():
        return None, None, None
    return int(parts[0]), parts[1], "_".join(parts[2:])


class FakeCoverPageExtractor(PublicationMetadataExtractor):
    """Prefers cover evidence, falls back to the file name, never merges the two."""

    @property
    def name(self) -> str:
        return "fake-cover"

    @property
    def version(self) -> str:
        return "0.1.0"

    def extract(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        source_path: Path | None = None,
    ) -> ExtractedPublicationMetadata:
        provenance = ExtractionProvenance(
            parser_name=self.name,
            parser_version=self.version,
            source_checksum=SOURCE_CHECKSUM,
        )
        filename_year, filename_author, filename_title = (
            _parse_filename(source_path) if source_path is not None else (None, None, None)
        )
        cover = pages[0] if pages else None

        title = self._resolve_title(cover, filename_title)
        authors = self._resolve_authors(cover, filename_author)
        dates = self._resolve_dates(cover, filename_year)

        return ExtractedPublicationMetadata(
            publication_id=publication.publication_id,
            provenance=provenance,
            values=[title],
            authors=authors,
            dates=dates,
        )

    def _resolve_title(
        self, cover: PublicationPage | None, filename_title: str | None
    ) -> ExtractedMetadataValue:
        if cover is not None:
            # Cover wins outright. The file-name reading is dropped, not merged.
            return ExtractedMetadataValue(
                field=MetadataField.TITLE,
                normalized=" ".join(cover.text.splitlines()[0].split()),
                evidence=MetadataEvidence(
                    source=MetadataEvidenceSource.COVER_PAGE,
                    raw_text=cover.text.splitlines()[0],
                    page_number=cover.page_number,
                ),
                confidence=0.9,
            )
        if filename_title is not None:
            return ExtractedMetadataValue(
                field=MetadataField.TITLE,
                normalized=filename_title,
                evidence=MetadataEvidence(
                    source=MetadataEvidenceSource.FILENAME,
                    raw_text=filename_title,
                ),
                confidence=0.3,
            )
        return ExtractedMetadataValue(
            field=MetadataField.TITLE,
            failure_reason="표지 페이지와 파일명 모두 없음",
        )

    def _resolve_authors(
        self, cover: PublicationPage | None, filename_author: str | None
    ) -> list[ExtractedAuthor]:
        cover_lines = cover.text.splitlines()[1:] if cover is not None else []
        named = [line.strip() for line in cover_lines if line.strip()]
        if named and cover is not None:
            return [
                ExtractedAuthor(
                    ordinal=index,
                    name=line,
                    is_primary=index == 0,
                    evidence=MetadataEvidence(
                        source=MetadataEvidenceSource.COVER_PAGE,
                        raw_text=line,
                        page_number=cover.page_number,
                    ),
                    confidence=0.8,
                )
                for index, line in enumerate(named)
            ]
        if filename_author is not None:
            return [
                ExtractedAuthor(
                    ordinal=0,
                    name=filename_author,
                    is_primary=True,
                    evidence=MetadataEvidence(
                        source=MetadataEvidenceSource.FILENAME,
                        raw_text=filename_author,
                    ),
                    confidence=0.3,
                )
            ]
        return [ExtractedAuthor(ordinal=0, failure_reason="저자 근거 없음")]

    def _resolve_dates(
        self, cover: PublicationPage | None, filename_year: int | None
    ) -> PublicationDates:
        stated = self._stated_year(cover)
        if stated is None:
            return PublicationDates(filename_year=filename_year)
        assert cover is not None
        return PublicationDates(
            filename_year=filename_year,
            published_at=date(stated, 1, 1),
            published_precision=DatePrecision.YEAR,
            date_evidence=MetadataEvidence(
                source=MetadataEvidenceSource.COVER_PAGE,
                raw_text=f"{stated}년",
                page_number=cover.page_number,
            ),
        )

    @staticmethod
    def _stated_year(cover: PublicationPage | None) -> int | None:
        if cover is None:
            return None
        for token in cover.text.replace("년", " ").split():
            if token.isdigit() and len(token) == 4:
                return int(token)
        return None


def _publication() -> ResearchPublication:
    return ResearchPublication(
        publication_id="pub-1",
        publication_type=PublicationType.RESEARCH_REPORT,
    )


TRUNCATED_FILENAME = Path("2019_김의순_국방분야실행아키텍처구현방안연ㄱ.pdf")


def test_cover_page_wins_over_a_truncated_filename() -> None:
    extractor = FakeCoverPageExtractor()
    pages = [_page("국방분야 실행 아키텍처 구현방안 연구")]

    metadata = extractor.extract(_publication(), pages, TRUNCATED_FILENAME)

    title = metadata.values[0]
    assert title.normalized == "국방분야 실행 아키텍처 구현방안 연구"
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.COVER_PAGE


def test_filename_reading_is_dropped_rather_than_merged() -> None:
    extractor = FakeCoverPageExtractor()
    pages = [_page("국방분야 실행 아키텍처 구현방안 연구")]

    metadata = extractor.extract(_publication(), pages, TRUNCATED_FILENAME)

    titles = [value for value in metadata.values if value.field is MetadataField.TITLE]
    assert len(titles) == 1
    assert "연ㄱ" not in str(titles[0].normalized)


def test_filename_is_used_only_when_no_cover_evidence_exists() -> None:
    extractor = FakeCoverPageExtractor()

    metadata = extractor.extract(_publication(), [], TRUNCATED_FILENAME)

    title = metadata.values[0]
    assert title.evidence is not None
    assert title.evidence.source is MetadataEvidenceSource.FILENAME
    assert title.confidence < 0.5


def test_filename_evidence_is_weaker_than_cover_evidence() -> None:
    extractor = FakeCoverPageExtractor()
    pages = [_page("국방분야 실행 아키텍처 구현방안 연구")]

    from_cover = extractor.extract(_publication(), pages, TRUNCATED_FILENAME)
    from_filename = extractor.extract(_publication(), [], TRUNCATED_FILENAME)

    cover_evidence = from_cover.values[0].evidence
    filename_evidence = from_filename.values[0].evidence
    assert cover_evidence is not None
    assert filename_evidence is not None
    assert cover_evidence.strength > filename_evidence.strength


def test_no_cover_and_no_filename_yields_an_explicit_failure() -> None:
    extractor = FakeCoverPageExtractor()

    metadata = extractor.extract(_publication(), [])

    title = metadata.values[0]
    assert title.normalized is None
    assert title.failure_reason == "표지 페이지와 파일명 모두 없음"


def test_multiple_cover_authors_are_kept_separately() -> None:
    extractor = FakeCoverPageExtractor()
    pages = [_page("연구 제목\n김의순\n오혜")]

    metadata = extractor.extract(_publication(), pages, TRUNCATED_FILENAME)

    assert [author.name for author in metadata.authors] == ["김의순", "오혜"]
    assert metadata.authors[0].is_primary
    assert not metadata.authors[1].is_primary


def test_filename_author_is_used_only_as_a_fallback() -> None:
    extractor = FakeCoverPageExtractor()

    metadata = extractor.extract(_publication(), [], TRUNCATED_FILENAME)

    assert [author.name for author in metadata.authors] == ["김의순"]
    evidence = metadata.authors[0].evidence
    assert evidence is not None
    assert evidence.source is MetadataEvidenceSource.FILENAME


def test_filename_year_and_body_year_conflict_is_surfaced_not_resolved() -> None:
    extractor = FakeCoverPageExtractor()
    pages = [_page("연구 제목 2018년 발간")]

    metadata = extractor.extract(_publication(), pages, TRUNCATED_FILENAME)

    assert metadata.dates.has_year_conflict
    assert metadata.dates.filename_year == 2019
    assert metadata.dates.published_at is not None
    assert metadata.dates.published_at.year == 2018


def test_unparseable_filename_contributes_nothing_instead_of_guessing() -> None:
    extractor = FakeCoverPageExtractor()

    metadata = extractor.extract(_publication(), [], Path("scan001.pdf"))

    assert metadata.values[0].normalized is None
    assert metadata.authors[0].name is None
    assert metadata.dates.filename_year is None


def test_extractor_identity_is_recorded_in_provenance() -> None:
    extractor = FakeCoverPageExtractor()

    metadata = extractor.extract(_publication(), [_page("제목")])

    assert metadata.provenance.parser_name == "fake-cover"
    assert metadata.provenance.parser_version == "0.1.0"
